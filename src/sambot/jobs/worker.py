"""RQ worker and job pipeline.

Complete story processing pipeline:
1. Fetch issue details from GitHub
2. Move project item to "In Progress"
3. Clone repo, create feature/bug branch from head of remote develop
4. Load memory
5. Run agent loop (multi-pass, language-agnostic, Docker-aware)
6. Commit, push, create PR (if tests pass)
7. Post PR to Slack, move to "In Review"
8. If blocked, move to "Blocked"
9. Compress and save new memory

Merge pipeline (triggered by PR approval):
1. Attempt rebase merge into develop
2. If clean → auto-complete
3. If complex → request new review
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import structlog

from sambot.db import get_session, init_db
from sambot.models import DockerPermission, JobStatus, StoryJob

logger = structlog.get_logger()


def _clone_repo(settings, branch: str | None = None) -> Path:
    """Clone the target repo and checkout the specified branch.

    Uses ``settings.sambot_base_branch`` (e.g. develop) when *branch*
    is not specified.  Handles dirty working trees left by interrupted
    runs by force-cleaning before checkout.

    Returns the work directory path.
    """
    if branch is None:
        branch = settings.sambot_base_branch

    work_dir = settings.sambot_work_dir / settings.github_repo_name
    repo_url = f"https://x-access-token:{settings.github_token}@github.com/{settings.github_repo}.git"

    if work_dir.exists():
        # Repo already cloned — force-clean any leftover state from
        # interrupted runs, then fetch and reset to latest remote.
        logger.info("worker.fetch_existing", work_dir=str(work_dir), branch=branch)

        # Abort any in-progress rebase/merge/cherry-pick
        for abort_cmd in ["rebase --abort", "merge --abort", "cherry-pick --abort"]:
            subprocess.run(
                ["git"] + abort_cmd.split(),
                cwd=work_dir, capture_output=True, timeout=10,
            )  # ignore errors — these are no-ops if nothing is in progress

        # Force-clean working tree (handles dirty state from aborted jobs)
        subprocess.run(
            ["git", "checkout", "-f"],
            cwd=work_dir, capture_output=True, timeout=30,
        )
        subprocess.run(
            ["git", "clean", "-fdx"],
            cwd=work_dir, capture_output=True, timeout=30,
        )

        # Fetch latest from remote
        subprocess.run(
            ["git", "fetch", "--all", "--prune"],
            cwd=work_dir, capture_output=True, check=True, timeout=120,
        )

        # Checkout the base branch (create tracking branch if needed)
        result = subprocess.run(
            ["git", "checkout", branch],
            cwd=work_dir, capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            # Branch may not exist locally yet — create from remote
            subprocess.run(
                ["git", "checkout", "-b", branch, f"origin/{branch}"],
                cwd=work_dir, capture_output=True, check=True, timeout=30,
            )

        # Reset to latest remote version
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=work_dir, capture_output=True, check=True, timeout=30,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=work_dir, capture_output=True, check=True, timeout=30,
        )
    else:
        # Fresh clone
        logger.info("worker.cloning", repo=settings.github_repo)
        work_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "-b", branch, repo_url, str(work_dir)],
            capture_output=True, check=True, timeout=300,
        )

    # Configure git identity for commits
    subprocess.run(
        ["git", "config", "user.name", "SamBot"],
        cwd=work_dir, capture_output=True, timeout=10,
    )
    subprocess.run(
        ["git", "config", "user.email", "sambot@noreply.github.com"],
        cwd=work_dir, capture_output=True, timeout=10,
    )

    return work_dir


def _create_feature_branch(work_dir: Path, branch_name: str, base: str = "develop") -> None:
    """Create and checkout a feature/bug branch from the base branch.

    If the requested base branch doesn't exist on the remote, falls back
    to ``origin/develop`` so the job never crashes on a missing branch.

    If the local branch already exists (stale from a previous run), it is
    deleted first so a fresh one can be created.
    """
    # Delete stale local branch if it already exists
    subprocess.run(
        ["git", "branch", "-D", branch_name],
        cwd=work_dir, capture_output=True, timeout=10,
    )  # ignore errors — branch may not exist

    result = subprocess.run(
        ["git", "checkout", "-b", branch_name, f"origin/{base}"],
        cwd=work_dir, capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        # Try the default base branch as fallback
        from sambot.config import get_settings
        fallback = get_settings().sambot_base_branch
        if base != fallback:
            logger.warning(
                "worker.base_branch_missing",
                branch=base,
                stderr=stderr,
            )
            subprocess.run(
                ["git", "checkout", "-b", branch_name, f"origin/{fallback}"],
                cwd=work_dir, capture_output=True, check=True, timeout=30,
            )
            logger.info("worker.branch_created", branch=branch_name, base=fallback, fallback=True)
        else:
            # Even the default branch failed
            result.check_returncode()
    else:
        logger.info("worker.branch_created", branch=branch_name, base=base)


def _commit_and_push(
    work_dir: Path,
    branch_name: str,
    message: str,
    files: list[str],
    base_branch: str = "develop",
) -> bool:
    """Stage changed files, commit if needed, and push to the feature branch.

    Raises RuntimeError if the feature branch has no commits ahead of
    *base_branch* — which would cause GitHub to reject the PR with 422.

    Returns True if push succeeded.
    """
    if branch_name.lower() in ("develop", "main", "master"):
        raise ValueError(f"Refusing to push to protected branch: {branch_name}")

    # Stage all changes (including new files)
    subprocess.run(
        ["git", "add", "-A"],
        cwd=work_dir, capture_output=True, check=True, timeout=30,
    )

    # Commit only if there are staged changes — the agent may have already
    # committed via run_command("git commit ..."), in which case we skip
    # the commit but still push below.
    status = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=work_dir, capture_output=True, timeout=15,
    )
    if status.returncode == 0:
        logger.info("worker.no_changes_to_commit")
    else:
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=work_dir, capture_output=True, check=True, timeout=30,
        )

    # Guard: verify the feature branch is actually ahead of the base branch.
    # If not, GitHub will reject create_pr with a 422 "no commits" error.
    log_result = subprocess.run(
        ["git", "log", f"origin/{base_branch}..HEAD", "--oneline"],
        cwd=work_dir, capture_output=True, text=True, timeout=15,
    )
    if not log_result.stdout.strip():
        raise RuntimeError(
            f"No commits between '{base_branch}' and '{branch_name}'. "
            "The agent did not make any code changes. "
            "Move the ticket back to Ready to retry."
        )

    ahead_count = len(log_result.stdout.strip().splitlines())
    logger.info("worker.commits_ahead", branch=branch_name, base=base_branch, count=ahead_count)

    # Push — branch is guaranteed to diverge from base.
    subprocess.run(
        ["git", "push", "origin", branch_name],
        cwd=work_dir, capture_output=True, check=True, timeout=120,
    )
    logger.info("worker.pushed", branch=branch_name)
    return True


def _push_wip_branch(work_dir: Path, branch_name: str, commit_message: str) -> None:
    """Stage any uncommitted changes, commit if needed, and push the branch.

    Used when blocked so the user can inspect the in-progress work.
    """
    if branch_name.lower() in ("develop", "main", "master"):
        raise ValueError(f"Refusing to push to protected branch: {branch_name}")

    # Stage everything
    subprocess.run(
        ["git", "add", "-A"],
        cwd=work_dir, capture_output=True, timeout=30,
    )

    # Commit only if there are staged changes
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=work_dir, capture_output=True, timeout=15,
    )
    if staged.returncode != 0:
        subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=work_dir, capture_output=True, check=True, timeout=30,
        )

    # Push (will create the remote branch if it doesn't exist yet)
    subprocess.run(
        ["git", "push", "origin", branch_name],
        cwd=work_dir, capture_output=True, check=True, timeout=120,
    )
    logger.info("worker.wip_pushed", branch=branch_name)


def _make_docker_permission_handler(settings, slack_app, work_dir=None):
    """Create a Docker permission handler that checks DB and asks via Slack.

    Args:
        work_dir: The cloned repo path. When provided the file content is
                  included as a code snippet in the Slack permission request.

    Returns a callable: (file_path: str, description: str) -> bool
    """

    def handler(file_path: str, description: str) -> bool:
        repo = settings.github_repo

        # Check DB for existing permission
        with get_session() as session:
            from sqlmodel import select
            stmt = select(DockerPermission).where(
                DockerPermission.repo == repo,
                DockerPermission.file_path == file_path,
                DockerPermission.approved == True,  # noqa: E712
            )
            existing = session.exec(stmt).first()
            if existing:
                logger.info("docker_permission.cached", file_path=file_path, repo=repo)
                return True

        # Not approved yet — ask via Slack
        if not slack_app:
            logger.warning("docker_permission.no_slack", file_path=file_path)
            return False

        try:
            from sambot.slack.questions import SlackQuestionHandler

            qa = SlackQuestionHandler(
                slack_app=slack_app,
                channel=settings.slack_questions_channel,
                timeout_minutes=settings.sambot_question_timeout_minutes,
            )

            question = (
                f"🐳 *Docker Permission Request*\n\n"
                f"The coder generated a new Docker file and needs permission to run it:\n\n"
                f"*File:* `{file_path}`\n"
                f"*Description:* {description}\n\n"
                f"Reply *approve* to allow running this file, or *deny* to block it."
            )

            # Read the file so the reviewer can see exactly what will run
            snippet: str | None = None
            if work_dir is not None:
                try:
                    from pathlib import Path as _Path
                    snippet = (_Path(work_dir) / file_path).read_text(errors="replace")
                except Exception:
                    pass  # file may not exist yet; proceed without snippet

            answer = qa.ask(question, context=f"Repo: {repo}", code_snippet=snippet)

            approved = any(
                word in answer.lower()
                for word in ("approve", "approved", "yes", "allow", "ok", "👍", "lgtm")
            )

            # Persist the decision
            with get_session() as session:
                perm = DockerPermission(
                    repo=repo,
                    file_path=file_path,
                    approved=approved,
                    approved_by="slack-user",
                    approved_at=datetime.now(UTC) if approved else None,
                )
                session.add(perm)
                session.commit()

            logger.info("docker_permission.decided", file_path=file_path, approved=approved)
            return approved

        except Exception as e:
            logger.exception("docker_permission.error", file_path=file_path)
            return False

    return handler


def process_story(issue_number: int) -> dict:
    """
    Background job: process a story end-to-end.

    This is the main entry point called by RQ.

    Pipeline:
    1. Fetch issue details from GitHub
    2. Move to "In Progress" on the project board
    3. Clone repo + create feature/bug branch from head of remote develop
    4. Run agent loop (language-agnostic, Docker-aware)
    5. If tests pass → commit, push, create PR
    6. Post PR to Slack, move to "In Review"
    7. If blocked → move to "Blocked"
    8. Compress memory
    """
    from sambot.config import get_settings
    from sambot.github.client import GitHubClient
    from sambot.github.pr import PRManager
    from sambot.github.projects import ProjectsClient
    from sambot.llm.client import LLMClient
    from sambot.slack.app import create_slack_app
    from sambot.slack.progress import SlackProgressReporter
    from sambot.slack.questions import SlackQuestionHandler

    settings = get_settings()
    from sambot.logging_config import configure_logging
    configure_logging(settings, log_filename="worker.log")
    logger.info("job.process_story.start", issue_number=issue_number)

    # Ensure DB is initialized in this worker process
    init_db()

    # Check retry count — if we've already failed MAX_RETRIES times,
    # move to Blocked instead of trying again.
    MAX_RETRIES = 3
    with get_session() as session:
        from sqlmodel import select
        past_failures = session.exec(
            select(StoryJob).where(
                StoryJob.issue_number == issue_number,
                StoryJob.status == JobStatus.FAILED,
            )
        ).all()
        if len(past_failures) >= MAX_RETRIES:
            logger.error(
                "job.max_retries_exceeded",
                issue_number=issue_number,
                retries=len(past_failures),
            )
            # Move to Blocked — do NOT move back to Ready
            import asyncio
            github = GitHubClient(settings)
            projects = ProjectsClient(
                github,
                owner=settings.resolved_project_owner,
                repo=settings.github_repo_name,
                project_number=settings.github_project_number,
            )
            async def _move_blocked():
                items = await projects.get_items()
                for item in items:
                    if item.issue_number == issue_number:
                        await projects.update_status(item.item_id, "Blocked")
                        return
            try:
                asyncio.run(_move_blocked())
            except Exception:
                logger.exception("job.move_blocked_failed", issue_number=issue_number)

            # Comment on the issue explaining why it's blocked
            last_errors = [j.error_message for j in past_failures if j.error_message][-3:]
            error_summary = "\n".join(f"- {e[:200]}" for e in last_errors) or "(no details recorded)"
            try:
                pr_manager = PRManager(github, base_branch=settings.sambot_base_branch)
                pr_manager.comment_on_issue(
                    issue_number,
                    f"🤖 **SamBot — Blocked after {len(past_failures)} failed attempts**\n\n"
                    f"This story has been moved to *Blocked* because it failed "
                    f"{len(past_failures)} times (max retries: {MAX_RETRIES}).\n\n"
                    f"**Recent errors:**\n{error_summary}\n\n"
                    f"To retry, move the item back to *Ready* on the project board.",
                )
            except Exception:
                logger.exception("job.blocked_comment_failed", issue_number=issue_number)

            return {
                "issue_number": issue_number,
                "status": "blocked",
                "error": f"Exceeded max retries ({MAX_RETRIES})",
            }

    # Create a job record
    with get_session() as session:
        job = StoryJob(issue_number=issue_number, issue_title="", status=JobStatus.RUNNING)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.id

    try:
        # Initialize clients
        github = GitHubClient(settings)
        pr_manager = PRManager(github, base_branch=settings.sambot_base_branch)
        llm = LLMClient(settings)

        # Slack setup
        slack_app = create_slack_app(settings)
        progress = SlackProgressReporter(slack_app, settings.slack_progress_channel)

        # 1. Fetch issue details
        issue = pr_manager.get_issue(issue_number)
        title = issue["title"]
        body = issue["body"]
        labels = issue["labels"]

        # Append any user comments to the body so the agent can see retry
        # instructions left by the user when they unblocked the ticket.
        try:
            comments = pr_manager.get_issue_comments(issue_number, limit=10)
            if comments:
                comments_text = "\n\n".join(
                    f"**@{c['author']} ({c['created_at'][:10]}):**\n{c['body']}"
                    for c in comments
                )
                body = body + f"\n\n---\n**Discussion / retry instructions:**\n{comments_text}"
                logger.info("worker.injected_comments", count=len(comments), issue_number=issue_number)
        except Exception:
            logger.exception("worker.fetch_comments_failed", issue_number=issue_number)

        # Update job record
        with get_session() as session:
            job = session.get(StoryJob, job_id)
            job.issue_title = title
            session.add(job)
            session.commit()

        progress.start_story(issue_number, title)
        progress.post(f"📋 Issue #{issue_number}: *{title}*")

        # 2. Move to "In Progress" on the project board
        import asyncio
        projects = ProjectsClient(
            github,
            owner=settings.resolved_project_owner,
            repo=settings.github_repo_name,
            project_number=settings.github_project_number,
        )

        async def _move_status(status_name: str):
            items = await projects.get_items()
            for item in items:
                if item.issue_number == issue_number:
                    await projects.update_status(item.item_id, status_name)
                    return
            logger.warning("worker.item_not_found", issue_number=issue_number, status=status_name)

        asyncio.run(_move_status("In progress"))
        progress.post("📊 Moved to *In progress*")

        # 3. Clone repo and create feature branch
        work_dir = _clone_repo(settings)
        branch_name = pr_manager.create_branch_name(issue_number, title, labels)

        # Always branch from the head of the remote develop branch
        base_branch = pr_manager.determine_base_branch()
        _create_feature_branch(work_dir, branch_name, base=base_branch)
        progress.post(f"🌿 Created branch `{branch_name}` from `{base_branch}`")

        # 4. Set up question and Docker permission handlers
        qa_handler = SlackQuestionHandler(
            slack_app=slack_app,
            channel=settings.slack_questions_channel,
            thread_ts=progress.thread_ts,
            timeout_minutes=settings.sambot_question_timeout_minutes,
        )
        docker_handler = _make_docker_permission_handler(settings, slack_app, work_dir)

        # 5. Run agent loop
        from sambot.agent.loop import AgentLoop

        agent = AgentLoop(
            work_dir=work_dir,
            anthropic_client=llm.raw_client,
            memory_path=settings.coding_memory_path,
            max_passes=settings.sambot_max_agent_passes,
            max_memory_tokens=settings.sambot_memory_max_tokens,
            model=llm.model,
            on_progress=progress.post,
            ask_question_handler=qa_handler.ask,
            docker_permission_handler=docker_handler,
        )

        result = agent.run(title, body, labels)

        # 6. Handle result
        if result.success:
            progress.post(result.summary)

            # Commit and push
            commit_msg = f"feat(#{issue_number}): {title}\n\nImplemented by SamBot"
            _commit_and_push(work_dir, branch_name, commit_msg, result.files_changed, base_branch=base_branch)
            progress.post(f"📤 Pushed to `{branch_name}`")

            # --- Post-push: PR creation and board update ---
            # Wrapped separately so a GitHub API hiccup here doesn't cause the
            # bot to re-implement an already-completed story.
            try:
                # Re-use an existing open PR if one was already created
                # (handles retries where push succeeded but PR creation failed).
                pr_number = pr_manager.find_open_pr_for_branch(branch_name)

                if pr_number:
                    progress.post(f"🔗 PR #{pr_number} already exists for `{branch_name}` — skipping creation")
                else:
                    # Generate PR description
                    from sambot.llm.prompts import PR_DESCRIPTION_SYSTEM
                    pr_body = llm.complete(
                        f"Story: {title}\n\nDescription: {body}\n\n"
                        f"Files changed: {', '.join(result.files_changed)}\n\n"
                        f"Test output:\n{result.test_output[:2000]}",
                        system=PR_DESCRIPTION_SYSTEM,
                    )

                    pr_number = pr_manager.create_pr(
                        title=f"feat(#{issue_number}): {title}",
                        body=pr_body,
                        head_branch=branch_name,
                        base_branch=None,
                        issue_number=issue_number,
                    )
                    progress.post(f"🔗 Created PR #{pr_number} → `{settings.sambot_base_branch}`")

                # Move to "In Review"
                asyncio.run(_move_status("In review"))
                progress.post("📊 Moved to *In review*")

                # Update job record
                with get_session() as session:
                    job = session.get(StoryJob, job_id)
                    job.status = JobStatus.SUCCESS
                    job.pr_number = pr_number
                    job.branch_name = branch_name
                    job.files_changed = ",".join(result.files_changed)
                    job.passes_used = result.passes_used
                    job.completed_at = datetime.now(UTC)
                    session.add(job)
                    session.commit()

                # Compress memory
                new_facts = (
                    f"Completed story #{issue_number}: {title}\n"
                    f"Branch: {branch_name}, PR: #{pr_number}\n"
                    f"Files: {', '.join(result.files_changed)}\n"
                    f"Passes: {result.passes_used}"
                )
                agent.compress_and_save_memory(llm, new_facts)

            except Exception as pr_err:
                logger.exception(
                    "worker.pr_creation_failed",
                    issue_number=issue_number,
                    branch=branch_name,
                )
                # Code is pushed — move to Blocked so a human can create the PR
                # manually. Do NOT move back to Ready (that would re-run the agent).
                try:
                    asyncio.run(_move_status("Blocked"))
                except Exception:
                    pass
                branch_url = f"https://github.com/{settings.github_repo}/tree/{branch_name}"
                try:
                    pr_manager.comment_on_issue(
                        issue_number,
                        f"🤖 SamBot finished coding but failed to create the PR.\n\n"
                        f"**Error:** {pr_err}\n\n"
                        f"The code is pushed to [`{branch_name}`]({branch_url}). "
                        f"Please create the PR manually or move back to *Ready* to retry.",
                    )
                except Exception:
                    pass
                with get_session() as session:
                    job = session.get(StoryJob, job_id)
                    job.status = JobStatus.FAILED
                    job.error_message = f"PR creation failed: {pr_err}"
                    job.branch_name = branch_name
                    job.files_changed = ",".join(result.files_changed)
                    job.passes_used = result.passes_used
                    job.completed_at = datetime.now(UTC)
                    session.add(job)
                    session.commit()
                return {
                    "issue_number": issue_number,
                    "status": "blocked",
                    "error": f"PR creation failed: {pr_err}",
                    "branch": branch_name,
                }

            return {
                "issue_number": issue_number,
                "status": "success",
                "pr_number": pr_number,
                "branch": branch_name,
            }

        else:
            # Failed / Blocked
            progress.post(result.summary)

            # Push WIP branch so the user can inspect the in-progress work
            branch_url: str | None = None
            try:
                _push_wip_branch(
                    work_dir,
                    branch_name,
                    f"wip(#{issue_number}): blocked after {result.passes_used} pass(es)",
                )
                branch_url = f"https://github.com/{settings.github_repo}/tree/{branch_name}"
                progress.post(f"📤 Pushed WIP branch `{branch_name}` to remote")
            except Exception:
                logger.exception("worker.wip_push_failed", branch=branch_name)

            # Move to "Blocked"
            asyncio.run(_move_status("Blocked"))
            progress.post("📊 Moved to *Blocked*")

            branch_ref = (
                f"\n\n**Branch:** [`{branch_name}`]({branch_url})\n"
                f"You can inspect the in-progress work there."
                if branch_url else ""
            )
            pr_manager.comment_on_issue(
                issue_number,
                f"🤖 SamBot was unable to complete this story.\n\n"
                f"**Error:** {result.error}\n"
                f"**Passes used:** {result.passes_used}\n"
                f"**Files changed:** {', '.join(result.files_changed) or 'none'}"
                f"{branch_ref}\n\n"
                f"To retry, reply here with any additional context or instructions, "
                f"then move this item back to *Ready* on the project board.",
            )

            # Update job record
            with get_session() as session:
                job = session.get(StoryJob, job_id)
                job.status = JobStatus.FAILED
                job.error_message = result.error
                job.passes_used = result.passes_used
                job.files_changed = ",".join(result.files_changed)
                job.completed_at = datetime.now(UTC)
                session.add(job)
                session.commit()

            return {
                "issue_number": issue_number,
                "status": "blocked",
                "error": result.error,
            }

    except Exception as e:
        logger.exception("job.process_story.error", issue_number=issue_number)

        # Move item back to Ready so it can be retried on next startup
        try:
            asyncio.run(_move_status("Ready"))
            logger.info("worker.moved_back_to_ready", issue_number=issue_number)

            # Signal the poller via Redis so it recycles this issue
            try:
                from redis import Redis
                redis_conn = Redis.from_url(settings.redis_url)
                redis_conn.sadd("sambot:retry_issues", issue_number)
                redis_conn.expire("sambot:retry_issues", 600)  # 10 min TTL
            except Exception:
                logger.exception("worker.retry_signal_failed", issue_number=issue_number)
        except Exception:
            logger.exception("worker.move_back_failed", issue_number=issue_number)

        # Update job record
        with get_session() as session:
            job = session.get(StoryJob, job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error_message = str(e)
                job.completed_at = datetime.now(UTC)
                session.add(job)
                session.commit()

        return {
            "issue_number": issue_number,
            "status": "error",
            "error": str(e),
        }


def merge_approved_pr(pr_number: int) -> dict:
    """
    Background job: merge an approved PR via rebase.

    Pipeline:
    1. Attempt rebase merge via GitHub API
    2. If clean → auto-complete
    3. If complex → attempt local rebase, then request new review if still failing
    """
    from sambot.config import get_settings
    from sambot.github.client import GitHubClient
    from sambot.github.pr import PRManager
    from sambot.slack.app import create_slack_app
    from sambot.slack.progress import SlackProgressReporter

    settings = get_settings()
    from sambot.logging_config import configure_logging
    configure_logging(settings, log_filename="worker.log")
    logger.info("job.merge_pr.start", pr_number=pr_number)

    # Ensure DB is initialized in this worker process
    init_db()

    try:
        github = GitHubClient(settings)
        pr_manager = PRManager(github, base_branch=settings.sambot_base_branch)
        slack_app = create_slack_app(settings)
        progress = SlackProgressReporter(slack_app, settings.slack_progress_channel)

        # Get PR info
        pr_info = pr_manager.get_pr(pr_number)
        progress.post(f"🔄 Attempting rebase merge of PR #{pr_number} (`{pr_info['head']}` → `{pr_info['base']}`)")

        # Attempt to use existing work dir for local rebase fallback
        work_dir = settings.sambot_work_dir / settings.github_repo_name
        if not work_dir.exists():
            work_dir = _clone_repo(settings)

        result = pr_manager.rebase_merge(pr_number, work_dir=work_dir)

        if result["success"]:
            if result["complex"]:
                progress.post(f"⚠️ PR #{pr_number} required local rebase but merged successfully.")
            else:
                progress.post(f"✅ PR #{pr_number} cleanly rebased and merged.")
        else:
            if result["complex"]:
                # Complex merge failed — request new review
                progress.post(
                    f"❌ PR #{pr_number} has merge conflicts. Requesting new review.\n"
                    f"Details: {result['message']}"
                )
                # Request a new review by commenting on the PR
                repo = github.repo
                pr = repo.get_pull(pr_number)
                pr.create_issue_comment(
                    "🤖 **SamBot**: This PR has rebase conflicts that need resolution. "
                    "Please review after conflicts are resolved."
                )
            else:
                progress.post(f"❌ PR #{pr_number} merge failed: {result['message']}")

        return {
            "pr_number": pr_number,
            "status": "merged" if result["success"] else "failed",
            "complex": result["complex"],
            "message": result["message"],
        }

    except Exception as e:
        logger.exception("job.merge_pr.error", pr_number=pr_number)
        return {
            "pr_number": pr_number,
            "status": "error",
            "error": str(e),
        }
