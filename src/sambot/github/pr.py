"""PR creation, branch management, and issue updates."""

from __future__ import annotations

import re
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
    """Handles pull request creation, branch management, and issue updates."""

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

    def create_branch(self, branch_name: str) -> None:
        """Create a new branch from the base branch (develop)."""
        repo = self._github.repo
        base_ref = repo.get_branch(self._base_branch)
        repo.create_git_ref(
            ref=f"refs/heads/{branch_name}",
            sha=base_ref.commit.sha,
        )
        logger.info("branch.created", branch=branch_name, base=self._base_branch)

    def create_pr(
        self,
        title: str,
        body: str,
        head_branch: str,
        issue_number: int | None = None,
    ) -> int:
        """Create a pull request targeting the base branch (develop).

        Returns the PR number.
        """
        repo = self._github.repo

        # Link to issue
        if issue_number:
            body += f"\n\nCloses #{issue_number}"

        pr = repo.create_pull(
            title=title,
            body=body,
            head=head_branch,
            base=self._base_branch,
        )

        logger.info("pr.created", pr_number=pr.number, title=title, base=self._base_branch)
        return pr.number

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
