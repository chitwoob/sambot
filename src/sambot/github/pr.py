"""PR creation, branch management, merge logic, and issue updates."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sambot.github.client import GitHubClient

logger = structlog.get_logger()


def slugify(text: str, max_length: int = 40) -> str:
    """Convert text to a URL-friendly slug."""
    slug = text.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug[:max_length].rstrip("-")


class PRManager:
    """Handles pull request creation, branch management, merges, and issue updates."""

    def __init__(self, github: GitHubClient, base_branch: str = "develop") -> None:
        self._github = github
        self._base_branch = base_branch

    def create_branch_name(self, issue_number: int, title: str, labels: list[str] | None = None) -> str:
        """Generate a branch name from issue details.

        Returns:
            feature/<num>-slug or bug/<num>-slug
        """
        prefix = "bug" if labels and "bug" in [lbl.lower() for lbl in labels] else "feature"
        slug = slugify(title)
        return f"{prefix}/{issue_number}-{slug}"

    def create_branch(self, branch_name: str, base: str | None = None) -> None:
        """Create a new branch from the specified base (default: develop).

        Args:
            branch_name: The new branch name.
            base: Branch to base from. Defaults to self._base_branch (develop).
        """
        repo = self._github.repo
        base_branch = base or self._base_branch
        base_ref = repo.get_branch(base_branch)
        repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=base_ref.commit.sha,
        )
        logger.info("branch.created", branch=branch_name, base=base_branch)

    def determine_base_branch(self) -> str:
        """Determine the best base branch for a new feature.

        If there are open PRs targeting develop (stacked reviews),
        the latest feature branch is returned so coder can stack on it.
        Otherwise, returns the default base branch (develop).

        The stacking branch must actually exist on the remote —
        we verify before returning it.

        Returns:
            Branch name to base new work from.
        """
        try:
            repo = self._github.repo
            open_prs = repo.get_pulls(state="open", base=self._base_branch, sort="created", direction="desc")
            latest_pr = None
            for pr in open_prs:
                # Pick the most recently created open PR targeting develop
                latest_pr = pr
                break

            if latest_pr:
                # Verify the branch still exists on the remote
                try:
                    repo.get_branch(latest_pr.head.ref)
                except Exception:
                    logger.warning(
                        "branch.stacking_branch_missing",
                        branch=latest_pr.head.ref,
                        pr=latest_pr.number,
                    )
                    return self._base_branch

                logger.info(
                    "branch.stacking",
                    base=latest_pr.head.ref,
                    stacked_on_pr=latest_pr.number,
                )
                return latest_pr.head.ref

        except Exception:
            logger.exception("branch.determine_base_error")

        return self._base_branch

    def create_pr(
        self,
        title: str,
        body: str,
        head_branch: str,
        base_branch: str | None = None,
        issue_number: int | None = None,
    ) -> int:
        """Create a pull request.

        PRs target develop or another feature branch, NEVER main.

        Returns the PR number.
        """
        repo = self._github.repo
        target = base_branch or self._base_branch

        # Safety: never target main
        if target.lower() in ("main", "master"):
            raise ValueError(f"Cannot create PR targeting protected branch '{target}'")

        # Link to issue
        if issue_number:
            body += f"\n\nCloses #{issue_number}"

        pr = repo.create_pull(
            title=title,
            body=body,
            head=head_branch,
            base=target,
        )

        logger.info("pr.created", pr_number=pr.number, title=title, base=target)
        return pr.number

    def rebase_merge(self, pr_number: int, work_dir: Path | None = None) -> dict:
        """Merge a PR using rebase strategy.

        Args:
            pr_number: The PR number to merge.
            work_dir: Local clone directory (for git operations if needed).

        Returns:
            dict with keys: success, complex, message
        """
        repo = self._github.repo
        pr = repo.get_pull(pr_number)

        # Check if PR is approved
        reviews = pr.get_reviews()
        is_approved = any(r.state == "APPROVED" for r in reviews)
        if not is_approved:
            return {
                "success": False,
                "complex": False,
                "message": f"PR #{pr_number} is not approved yet.",
            }

        # Safety: never merge into main
        base = pr.base.ref
        if base.lower() in ("main", "master"):
            return {
                "success": False,
                "complex": False,
                "message": f"Cannot merge into protected branch '{base}'.",
            }

        # Try GitHub API rebase merge first
        try:
            pr.merge(merge_method="rebase")
            logger.info("pr.merged", pr_number=pr_number, method="rebase", base=base)
            return {
                "success": True,
                "complex": False,
                "message": f"PR #{pr_number} successfully rebased and merged into {base}.",
            }
        except Exception as e:
            error_msg = str(e)
            logger.warning("pr.merge_api_failed", pr_number=pr_number, error=error_msg)

        # If API merge fails, try local rebase (complex merge)
        if work_dir and work_dir.exists():
            return self._local_rebase_merge(pr_number, pr, work_dir)

        return {
            "success": False,
            "complex": True,
            "message": (
                f"PR #{pr_number} has conflicts that need manual resolution. "
                f"Rebase merge failed: {error_msg}"
            ),
        }

    def _local_rebase_merge(self, pr_number: int, pr, work_dir: Path) -> dict:
        """Attempt a local git rebase for complex merges.

        Returns:
            dict with keys: success, complex, message
        """
        head = pr.head.ref
        base = pr.base.ref

        try:
            # Fetch latest
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=work_dir, capture_output=True, check=True, timeout=60,
            )

            # Checkout the feature branch
            subprocess.run(
                ["git", "checkout", head],
                cwd=work_dir, capture_output=True, check=True, timeout=30,
            )

            # Attempt rebase
            rebase_result = subprocess.run(
                ["git", "rebase", f"origin/{base}"],
                cwd=work_dir, capture_output=True, text=True, timeout=120,
            )

            if rebase_result.returncode != 0:
                # Rebase failed — abort and report as complex
                subprocess.run(
                    ["git", "rebase", "--abort"],
                    cwd=work_dir, capture_output=True, timeout=30,
                )
                return {
                    "success": False,
                    "complex": True,
                    "message": (
                        f"PR #{pr_number} rebase has conflicts. "
                        f"Output: {rebase_result.stderr[:500]}"
                    ),
                }

            # Push the rebased branch
            subprocess.run(
                ["git", "push", "--force-with-lease", "origin", head],
                cwd=work_dir, capture_output=True, check=True, timeout=60,
            )

            # Now try the API merge again (should be clean after rebase)
            pr.merge(merge_method="rebase")
            logger.info("pr.merged_local_rebase", pr_number=pr_number, base=base)
            return {
                "success": True,
                "complex": True,
                "message": (
                    f"PR #{pr_number} required local rebase but merged successfully into {base}."
                ),
            }

        except Exception as e:
            logger.exception("pr.local_rebase_error", pr_number=pr_number)
            return {
                "success": False,
                "complex": True,
                "message": f"PR #{pr_number} local rebase failed: {e}",
            }

    def comment_on_issue(self, issue_number: int, body: str) -> None:
        """Add a comment to an issue."""
        repo = self._github.repo
        issue = repo.get_issue(issue_number)
        issue.create_comment(body)
        logger.info("issue.commented", issue_number=issue_number)

    def get_issue(self, issue_number: int) -> dict:
        """Fetch issue details."""
        repo = self._github.repo
        issue = repo.get_issue(issue_number)
        return {
            "number": issue.number,
            "title": issue.title,
            "body": issue.body or "",
            "labels": [label.name for label in issue.labels],
            "state": issue.state,
        }

    def get_pr(self, pr_number: int) -> dict:
        """Fetch PR details."""
        repo = self._github.repo
        pr = repo.get_pull(pr_number)
        reviews = pr.get_reviews()
        review_states = [r.state for r in reviews]
        return {
            "number": pr.number,
            "title": pr.title,
            "state": pr.state,
            "head": pr.head.ref,
            "base": pr.base.ref,
            "mergeable": pr.mergeable,
            "approved": "APPROVED" in review_states,
            "review_states": review_states,
        }
