"""Agent ↔ human Q&A via Slack.

The agent can ask technical or business questions during its coding loop.
Questions are posted to the Slack progress channel in a thread.
The agent pauses until a human responds (or times out).
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from slack_bolt import App

logger = structlog.get_logger()


class SlackQuestionHandler:
    """Posts agent questions to Slack and waits for human answers."""

    def __init__(
        self,
        slack_app: App | None,
        channel: str,
        thread_ts: str | None = None,
        timeout_minutes: int = 30,
    ) -> None:
        self._app = slack_app
        self._channel = channel
        self._thread_ts = thread_ts
        self._timeout_minutes = timeout_minutes
        self._pending_answer: str | None = None
        self._answer_event = threading.Event()

    def ask(self, question: str, context: str = "") -> str:
        """
        Post a question to Slack and wait for a human answer.

        Args:
            question: The question to ask
            context: Additional context about why the agent is asking

        Returns:
            The human's answer, or a timeout message
        """
        if not self._app:
            logger.warning("slack_qa.no_app", msg="Slack not configured, using default")
            return "No Slack connection available. Proceed with your best judgment."

        # Build the question message
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🤖 *SamBot Agent has a question:*\n\n{question}",
                },
            },
        ]
        if context:
            blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"_Context: {context}_"},
                ],
            })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Reply in this thread to answer. The agent is waiting...",
            },
        })

        try:
            # Post the question
            result = self._app.client.chat_postMessage(
                channel=self._channel,
                blocks=blocks,
                text=f"SamBot question: {question}",
                thread_ts=self._thread_ts,
            )

            question_ts = result["ts"]
            # Use the channel ID from the response (not the name we passed in),
            # because conversations_replies requires a channel ID.
            self._resolved_channel = result["channel"]
            if not self._thread_ts:
                self._thread_ts = question_ts

            logger.info("slack_qa.asked", question=question[:80], ts=question_ts)

            # Wait for answer
            self._pending_answer = None
            self._answer_event.clear()

            # Register a temporary listener for replies in this thread
            answer = self._wait_for_reply(question_ts)

            if answer:
                logger.info("slack_qa.answered", answer=answer[:80])
                return answer
            else:
                timeout_msg = (
                    f"No response received within {self._timeout_minutes} minutes. "
                    "Proceeding with best judgment."
                )
                # Post timeout notification
                self._app.client.chat_postMessage(
                    channel=self._channel,
                    text=f"⏰ {timeout_msg}",
                    thread_ts=self._thread_ts,
                )
                return timeout_msg

        except Exception as e:
            logger.error("slack_qa.error", error=str(e))
            return f"Error posting question to Slack: {e}. Proceeding with best judgment."

    def _wait_for_reply(self, question_ts: str) -> str | None:
        """Poll for a reply in the thread. Returns the reply text or None on timeout."""
        deadline = time.time() + (self._timeout_minutes * 60)
        poll_interval = 5  # seconds
        # Use resolved channel ID (from postMessage response) for reads
        read_channel = getattr(self, "_resolved_channel", None) or self._channel
        consecutive_errors = 0
        max_consecutive_errors = 5

        while time.time() < deadline:
            try:
                # Check for new replies in the thread
                replies = self._app.client.conversations_replies(
                    channel=read_channel,
                    ts=self._thread_ts or question_ts,
                    oldest=question_ts,
                )

                consecutive_errors = 0  # reset on success
                messages = replies.get("messages", [])
                # Skip the first message (our question) and bot messages
                for msg in messages[1:]:
                    if msg.get("ts") > question_ts and not msg.get("bot_id"):
                        return msg.get("text", "")

            except Exception as e:
                consecutive_errors += 1
                logger.warning("slack_qa.poll_error", error=str(e))
                if consecutive_errors >= max_consecutive_errors:
                    logger.error(
                        "slack_qa.poll_giving_up",
                        channel=read_channel,
                        errors=consecutive_errors,
                    )
                    return None

            time.sleep(poll_interval)

        return None

    @property
    def thread_ts(self) -> str | None:
        """Get the current thread timestamp."""
        return self._thread_ts

    @thread_ts.setter
    def thread_ts(self, value: str | None) -> None:
        """Set the thread timestamp."""
        self._thread_ts = value
