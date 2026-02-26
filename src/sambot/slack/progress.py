"""Agent progress streaming to Slack."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from slack_bolt import App

logger = structlog.get_logger()


class SlackProgressReporter:
    """Streams agent progress updates to a Slack channel/thread."""

    def __init__(
        self,
        slack_app: App | None,
        channel: str,
        thread_ts: str | None = None,
    ) -> None:
        self._app = slack_app
        self._channel = channel
        self._thread_ts = thread_ts

    def post(self, message: str) -> None:
        """Post a progress message to Slack."""
        if not self._app:
            logger.info("progress.no_slack", message=message)
            return

        try:
            result = self._app.client.chat_postMessage(
                channel=self._channel,
                text=message,
                thread_ts=self._thread_ts,
            )
            # Set thread_ts on first message to create a thread
            if not self._thread_ts:
                self._thread_ts = result["ts"]

            logger.info("progress.posted", message=message[:80])
        except Exception as e:
            logger.error("progress.error", error=str(e), message=message[:80])

    def start_story(self, issue_number: int, title: str) -> str | None:
        """Post the initial story message and return the thread_ts."""
        self.post(f"🚀 *Starting work on #{issue_number}: {title}*")
        return self._thread_ts

    @property
    def thread_ts(self) -> str | None:
        return self._thread_ts

    @thread_ts.setter
    def thread_ts(self, value: str | None) -> None:
        self._thread_ts = value
