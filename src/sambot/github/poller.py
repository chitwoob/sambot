"""GitHub polling — periodically checks project board for status changes.

Replaces webhooks so SamBot can run behind NAT/firewalls without
inbound connectivity.  The poller fetches project items on a configurable
interval and detects stories in the "Ready" status.  Items are picked in
priority order (top-to-bottom as they appear on the board).

It also watches for PR approvals on open PRs so the coder can
auto-merge when a review is approved.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Callable

    from sambot.config import Settings
    from sambot.github.client import GitHubClient
    from sambot.github.projects import ProjectItem, ProjectsClient

logger = structlog.get_logger()

# Status that triggers the agent to pick up work
TRIGGER_STATUS = "ready"

# Redis key where the worker signals retryable issues
RETRY_ISSUES_KEY = "sambot:retry_issues"


class GitHubPoller:
    """Polls GitHub Projects V2 for stories in "Ready" status.

    When a project item is in the trigger status (default "Ready"),
    the registered callback is invoked with the ``ProjectItem``.  Items
    are picked in the order returned by the API (board priority: top-to-bottom).
    Each item is only triggered once — the poller tracks which issue numbers
    it has already dispatched.

    Also polls for PR approvals to enable auto-merge.
    """

    def __init__(
        self,
        settings: Settings,
        github: GitHubClient,
        projects: ProjectsClient,
        *,
        on_trigger: Callable[[ProjectItem], None] | None = None,
        on_pr_approved: Callable[[int], None] | None = None,
        trigger_status: str = TRIGGER_STATUS,
    ) -> None:
        self._settings = settings
        self._github = github
        self._projects = projects
        self._on_trigger = on_trigger
        self._on_pr_approved = on_pr_approved
        self._trigger_status = trigger_status.lower()
        self._poll_interval: int = settings.sambot_poll_interval
        self._seen_issues: set[int] = set()
        self._seen_approved_prs: set[int] = set()
        self._running = False
        # Track issues that left Ready (moved to In Progress, etc.)
        # so we can re-trigger them if they come back to Ready.
        self._left_ready: set[int] = set()

    # -- public API ----------------------------------------------------------

    async def start(self) -> None:
        """Start the polling loop.  Runs until ``stop()`` is called."""
        self._running = True
        logger.info(
            "poller.starting",
            interval=self._poll_interval,
            trigger_status=self._trigger_status,
        )

        while self._running:
            try:
                await self._poll()
            except Exception:
                logger.exception("poller.error")

            try:
                await self._poll_pr_approvals()
            except Exception:
                logger.exception("poller.pr_approval_error")

            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        """Signal the polling loop to stop after its current iteration."""
        self._running = False
        logger.info("poller.stopping")

    @property
    def seen_issues(self) -> set[int]:
        """Issue numbers already dispatched (read-only copy)."""
        return set(self._seen_issues)

    def mark_seen(self, issue_number: int) -> None:
        """Manually mark an issue so the poller won't trigger it again."""
        self._seen_issues.add(issue_number)

    # -- internals -----------------------------------------------------------

    async def _poll(self) -> None:
        """Fetch project items and fire callback for the highest-priority Ready item.

        Items are returned in board order (top-to-bottom = priority order).
        We only pick ONE item per poll cycle to avoid overwhelming the agent.
        """
        # On first poll, load status field metadata
        if not hasattr(self, "_field_metadata_loaded"):
            self._field_metadata_loaded = True
            try:
                await self._projects.load_field_metadata()
            except Exception:
                logger.exception("poller.field_metadata_error")

        items = await self._projects.get_items()

        # Build status map for all items
        status_by_issue = {
            item.issue_number: item.status.lower()
            for item in items
        }

        # Track items that have left Ready since we dispatched them
        for issue_num in list(self._seen_issues):
            current = status_by_issue.get(issue_num, "")
            if current and current != self._trigger_status:
                self._left_ready.add(issue_num)

        # Recycle: if an item we dispatched has LEFT Ready and COME BACK,
        # allow it to be triggered again (worker crashed and moved it back).
        ready_issues = {
            item.issue_number
            for item in items
            if item.status.lower() == self._trigger_status
        }
        recycled = self._left_ready & ready_issues
        if recycled:
            logger.info("poller.recycling_seen", issues=sorted(recycled))
            self._seen_issues -= recycled
            self._left_ready -= recycled

        # Also check Redis for retry signals from the worker.
        # This handles the case where the worker round-trips
        # (Ready -> In Progress -> Ready) within one poll cycle.
        try:
            from redis import Redis
            redis_conn = Redis.from_url(self._settings.redis_url)
            retry_raw = redis_conn.smembers(RETRY_ISSUES_KEY)
            if retry_raw:
                retry_issues = {int(x) for x in retry_raw}
                redis_recycled = retry_issues & self._seen_issues & ready_issues
                if redis_recycled:
                    logger.info("poller.redis_recycling", issues=sorted(redis_recycled))
                    self._seen_issues -= redis_recycled
                    self._left_ready -= redis_recycled
                    # Remove consumed signals
                    for inum in redis_recycled:
                        redis_conn.srem(RETRY_ISSUES_KEY, inum)
        except Exception:
            logger.exception("poller.redis_retry_check_error")

        for item in items:
            if item.status.lower() != self._trigger_status:
                continue
            if item.issue_number in self._seen_issues:
                continue

            # Found the highest-priority Ready item — trigger it
            logger.info(
                "poller.triggered",
                issue_number=item.issue_number,
                title=item.title,
                status=item.status,
            )
            self._seen_issues.add(item.issue_number)

            if self._on_trigger is not None:
                try:
                    self._on_trigger(item)
                except Exception:
                    logger.exception(
                        "poller.callback_error",
                        issue_number=item.issue_number,
                    )

            # Only pick one per cycle
            break

    async def _poll_pr_approvals(self) -> None:
        """Check open PRs for approved reviews.

        When a PR created by the bot gets approved, fire the
        on_pr_approved callback so the worker can handle the merge.
        """
        if not self._on_pr_approved:
            return

        try:
            repo = self._github.repo
            open_prs = repo.get_pulls(state="open", base=self._settings.sambot_base_branch)

            for pr in open_prs:
                if pr.number in self._seen_approved_prs:
                    continue

                reviews = pr.get_reviews()
                for review in reviews:
                    if review.state == "APPROVED":
                        logger.info("poller.pr_approved", pr_number=pr.number)
                        self._seen_approved_prs.add(pr.number)
                        try:
                            self._on_pr_approved(pr.number)
                        except Exception:
                            logger.exception("poller.pr_approval_callback_error", pr_number=pr.number)
                        break
        except Exception:
            logger.exception("poller.pr_approval_poll_error")
