"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI
from slack_bolt.adapter.socket_mode import SocketModeHandler

from sambot.config import get_settings
from sambot.db import init_db

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger()

# Module-level reference so other parts of the app can stop it if needed
_poller_task: asyncio.Task | None = None


def _default_coding_memory(settings) -> str:
    """Generate a default coding agent memory file."""
    return (
        "# SamBot — Coding Agent Memory\n\n"
        "> Persistent context for the AI coding agent.\n"
        "> Updated automatically as stories are completed.\n\n"
        "---\n\n"
        "## Project Info\n\n"
        f"**Repository:** {settings.github_repo}\n"
        f"**Base Branch:** {settings.sambot_base_branch}\n"
        f"**Max Agent Passes:** {settings.sambot_max_agent_passes}\n\n"
        "---\n\n"
        "## Architecture\n\n"
        "_No facts recorded yet. This file will be updated as stories are completed._\n\n"
        "## Conventions\n\n"
        "_Will be populated after the first coding run._\n"
    )


def _default_backlog_memory(settings) -> str:
    """Generate a default backlog agent memory file."""
    return (
        "# SamBot — Backlog Agent Memory\n\n"
        "> Persistent context for the backlog/story-building agent.\n"
        "> Updated as stories are refined and created.\n\n"
        "---\n\n"
        "## Project Info\n\n"
        f"**Repository:** {settings.github_repo}\n\n"
        "## Story Conventions\n\n"
        "_No conventions recorded yet. Will be populated as stories are refined._\n\n"
        "## Labels\n\n"
        "- `feature` — New functionality\n"
        "- `bug` — Defect fix\n"
        "- `improvement` — Enhancement to existing code\n"
        "- `chore` — Maintenance / tooling\n"
    )


async def _recover_interrupted_jobs(settings, projects) -> None:
    """Recover items stuck in 'In progress' from a previous interrupted run.

    On startup, fetch all project items and find ones in "In progress".
    For each, check if there's an active RQ job for that issue.  If not,
    move the item back to "Ready" so it gets picked up again.
    """
    try:
        items = await projects.get_items()
        in_progress = [i for i in items if i.status.lower() == "in progress"]

        if not in_progress:
            logger.info("recovery.none_needed")
            return

        # Check RQ for active jobs
        from redis import Redis
        from rq import Queue

        redis_conn = Redis.from_url(settings.redis_url)
        queue = Queue(connection=redis_conn)

        # Collect issue numbers that have active (queued/started) RQ jobs
        active_issues: set[int] = set()
        for job in queue.jobs:
            if job.func_name == "sambot.jobs.worker.process_story" and job.args:
                active_issues.add(job.args[0])

        # Also check the started job registry
        started = queue.started_job_registry
        for job_id in started.get_job_ids():
            try:
                from rq.job import Job
                job = Job.fetch(job_id, connection=redis_conn)
                if job.func_name == "sambot.jobs.worker.process_story" and job.args:
                    active_issues.add(job.args[0])
            except Exception:
                pass  # job may have been cleaned up

        for item in in_progress:
            if item.issue_number in active_issues:
                logger.info(
                    "recovery.job_still_active",
                    issue_number=item.issue_number,
                    title=item.title,
                )
                continue

            # No active job — move back to Ready
            logger.info(
                "recovery.moving_to_ready",
                issue_number=item.issue_number,
                title=item.title,
            )
            try:
                await projects.update_status(item.item_id, "Ready")
                logger.info(
                    "recovery.recovered",
                    issue_number=item.issue_number,
                )
            except Exception:
                logger.exception(
                    "recovery.move_failed",
                    issue_number=item.issue_number,
                )

    except Exception:
        logger.exception("recovery.error")


def _reset_failed_job_records() -> None:
    """Clear stale FAILED job records so the retry counter starts fresh.

    Called once on startup — previous failures from earlier bot runs
    should not count against the retry limit for the new session.
    """
    from sambot.db import get_session
    from sambot.models import JobStatus, StoryJob
    from sqlmodel import select

    with get_session() as session:
        stale = session.exec(
            select(StoryJob).where(StoryJob.status == JobStatus.FAILED)
        ).all()
        if not stale:
            return
        for job in stale:
            job.status = JobStatus.CANCELLED
            session.add(job)
        session.commit()
        logger.info("recovery.reset_failed_jobs", count=len(stale))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application startup and shutdown."""
    global _poller_task  # noqa: PLW0603

    settings = get_settings()

    # Configure logging
    logging.basicConfig(level=getattr(logging, settings.sambot_log_level.upper()))
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.sambot_log_level.upper())
        ),
    )

    logger.info("sambot.starting", repo=settings.github_repo)

    # Ensure data and work directories exist
    settings.sambot_data_dir.mkdir(parents=True, exist_ok=True)
    settings.sambot_work_dir.mkdir(parents=True, exist_ok=True)

    # Seed coding memory — copy bundled file or generate a default
    if not settings.coding_memory_path.exists():
        bundled = Path("/app/MEMORY.md")
        if bundled.exists():
            import shutil
            shutil.copy2(bundled, settings.coding_memory_path)
            logger.info("sambot.memory_seeded", source="bundled", path=str(settings.coding_memory_path))
        else:
            settings.coding_memory_path.write_text(_default_coding_memory(settings))
            logger.info("sambot.memory_created", path=str(settings.coding_memory_path))

    # Seed backlog memory — generate a default if missing
    if not settings.backlog_memory_path.exists():
        settings.backlog_memory_path.write_text(_default_backlog_memory(settings))
        logger.info("sambot.backlog_memory_created", path=str(settings.backlog_memory_path))

    # Initialize database
    init_db(str(settings.database_path))
    logger.info("sambot.db_initialized", path=str(settings.database_path))

    # Reset stale failure records so retry counter starts fresh
    _reset_failed_job_records()

    # Start GitHub poller as a background task
    from sambot.github.client import GitHubClient
    from sambot.github.poller import GitHubPoller
    from sambot.github.projects import ProjectsClient

    github = GitHubClient(settings)
    projects = ProjectsClient(
        github,
        owner=settings.resolved_project_owner,
        repo=settings.github_repo_name,
        project_number=settings.github_project_number,
    )

    # Recover interrupted jobs — move orphaned "In progress" items back to Ready
    await _recover_interrupted_jobs(settings, projects)

    def _on_trigger(item):
        """Callback when a story is in Ready status — enqueue for processing."""
        logger.info("poller.dispatch", issue_number=item.issue_number, title=item.title)
        try:
            from redis import Redis
            from rq import Queue

            redis_conn = Redis.from_url(settings.redis_url)
            queue = Queue(connection=redis_conn)

            from sambot.jobs.worker import process_story
            queue.enqueue(process_story, item.issue_number, job_timeout="30m")
            logger.info("poller.enqueued", issue_number=item.issue_number)
        except Exception:
            logger.exception("poller.enqueue_error", issue_number=item.issue_number)

    def _on_pr_approved(pr_number):
        """Callback when a PR is approved — enqueue merge job."""
        logger.info("poller.pr_approved_dispatch", pr_number=pr_number)
        try:
            from redis import Redis
            from rq import Queue

            redis_conn = Redis.from_url(settings.redis_url)
            queue = Queue(connection=redis_conn)

            from sambot.jobs.worker import merge_approved_pr
            queue.enqueue(merge_approved_pr, pr_number, job_timeout="10m")
            logger.info("poller.merge_enqueued", pr_number=pr_number)
        except Exception:
            logger.exception("poller.merge_enqueue_error", pr_number=pr_number)

    poller = GitHubPoller(
        settings, github, projects,
        on_trigger=_on_trigger,
        on_pr_approved=_on_pr_approved,
    )
    _poller_task = asyncio.create_task(poller.start())
    logger.info("sambot.poller_started", interval=settings.sambot_poll_interval)

    # Start Slack app in socket mode (runs in a background thread)
    from sambot.slack.app import create_slack_app, start_socket_mode

    slack_app = create_slack_app(settings)
    slack_handler = None
    if slack_app:
        from sambot.slack.backlog_handler import register_backlog_handler

        register_backlog_handler(slack_app, settings)
        slack_handler = SocketModeHandler(slack_app, settings.slack_app_token)
        slack_handler.connect()  # non-blocking — runs in a daemon thread
        logger.info("sambot.slack_started")
    else:
        logger.warning("sambot.slack_disabled")

    yield

    # Shutdown
    if slack_handler:
        slack_handler.close()
    poller.stop()
    if _poller_task:
        _poller_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _poller_task
    logger.info("sambot.shutdown")


app = FastAPI(
    title="SamBot",
    description="SDLC workflow automation",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}
