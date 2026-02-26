"""GitHub polling — periodically checks project board for status changes.

Replaces webhooks so SamBot can run behind NAT/firewalls without
inbound connectivity.  The poller fetches project items on a configurable
interval and detects when stories move to "In Progress".
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

# Status value that triggers the agent (case-insensitive comparison)
TRIGGER_STATUS = "in progress"


class GitHubPoller:
    """Polls GitHub Projects V2 for status changes on a fixed interval.

    When a project item moves to the trigger status (default "In Progress"),
    the registered callback is invoked with the ``ProjectItem``.  Each item
    is only triggered once — the poller tracks which issue numbers it has
    already dispatched.
    """

    def __init__(
        self,
        settings: Settings,
        github: GitHubClient,
        projects: ProjectsClient,
        *,
        on_trigger: Callable[[ProjectItem], None] | None = None,
        trigger_status: str = TRIGGER_STATUS,
    ) -> None:
        self._settings = settings
        self._github = github
        self._projects = projects
        self._on_trigger = on_trigger
        self._trigger_status = trigger_status.lower()
        self._poll_interval: int = settings.sambot_poll_interval
        self._seen_issues: set[int] = set()
        self._running = False

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
        """Fetch project items and fire callback for newly triggered ones."""
        items = await self._projects.get_items()

        for item in items:
            if item.status.lower() != self._trigger_status:
                continue
            if item.issue_number in self._seen_issues:
                continue

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
