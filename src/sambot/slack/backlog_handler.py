"""Slack message handler for the #sambot-backlog channel.

Listens for messages in the backlog channel and invokes the BacklogAgent
to refine ideas into structured stories.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from slack_bolt import App

    from sambot.config import Settings

logger = structlog.get_logger()


def register_backlog_handler(app: App, settings: Settings) -> None:
    """Register a message listener for the backlog channel."""

    backlog_channel = settings.slack_backlog_channel

    @app.event("message")
    def handle_message(event, say, client):
        """Handle messages in the backlog channel."""
        # Only respond to messages in the backlog channel
        channel = event.get("channel", "")
        subtype = event.get("subtype")
        text = event.get("text", "").strip()
        user = event.get("user", "")

        # Skip bot messages, message edits, and empty messages
        if subtype or not text or not user:
            return

        # Resolve channel name — event contains the channel ID, not the name
        try:
            info = client.conversations_info(channel=channel)
            channel_name = info["channel"]["name"]
        except Exception:
            logger.debug("backlog.channel_lookup_failed", channel=channel)
            return

        if channel_name != backlog_channel:
            return

        logger.info("backlog.message_received", user=user, text=text[:80])

        # Check if this is a thread reply (follow-up answer)
        thread_ts = event.get("thread_ts")

        try:
            # Initialize LLM client and backlog agent
            from sambot.agent.backlog import BacklogAgent
            from sambot.llm.client import LLMClient

            llm = LLMClient(settings)
            agent = BacklogAgent(llm_client=llm, settings=settings)

            if thread_ts:
                # This is a reply in a thread — gather conversation context
                replies = client.conversations_replies(
                    channel=channel, ts=thread_ts
                )
                messages = replies.get("messages", [])
                context = "\n".join(
                    f"<@{m.get('user', 'bot')}>: {m.get('text', '')}"
                    for m in messages[:-1]  # everything before this message
                )

                # Classify intent: create the ticket or refine further?
                intent = agent.classify_intent(text, conversation_context=context)

                if intent == "create":
                    # Re-refine from the full thread context to get
                    # a clean structured story, then add to the backlog.
                    story = agent.refine_idea(
                        text, conversation_context=context
                    )
                    result = agent.create_backlog_item(story)
                    kind = _item_kind(story)
                    response = (
                        f":white_check_mark: *{kind} added to the backlog!*\n\n"
                        f"<{result['url']}|{result['title']}>"
                    )
                    logger.info(
                        "backlog.item_created_from_thread",
                        title=result["title"],
                        url=result["url"],
                    )
                else:
                    story = agent.refine_idea(text, conversation_context=context)
                    response = _format_story(story)
            else:
                story = agent.refine_idea(text)
                response = _format_story(story)

            # Reply in a thread
            say(text=response, thread_ts=thread_ts or event.get("ts"))

        except Exception as e:
            logger.error("backlog.error", error=str(e))
            say(
                text=f"Sorry, I ran into an error processing that: {e}",
                thread_ts=event.get("ts"),
            )


def _format_story(story: dict) -> str:
    """Format a parsed story dict into a Slack message."""
    parts = []

    kind = _item_kind(story)
    if story.get("title"):
        parts.append(f"*📋 {story['title']}*")

    if story.get("description"):
        parts.append(f"\n{story['description']}")

    if story.get("acceptance_criteria"):
        parts.append("\n*Acceptance Criteria:*")
        for ac in story["acceptance_criteria"]:
            parts.append(f"  • {ac}")

    if story.get("labels"):
        labels = ", ".join(f"`{lb}`" for lb in story["labels"])
        parts.append(f"\n*Labels:* {labels}")

    if story.get("follow_up_questions"):
        parts.append("\n*❓ Follow-up Questions:*")
        for q in story["follow_up_questions"]:
            parts.append(f"  • {q}")

    # Always prompt for approval
    parts.append("\n_Reply *create it* to submit this ticket, or provide feedback to refine._")

    return "\n".join(parts) if parts else story.get("raw", "I couldn't parse a story from that.")


_LABEL_TO_KIND = {
    "bug": "Bug",
    "feature": "Story",
    "story": "Story",
    "improvement": "Story",
    "chore": "Task",
    "task": "Task",
}


def _item_kind(story: dict) -> str:
    """Derive a human-friendly type (Story, Task, Bug) from labels."""
    for label in story.get("labels", []):
        kind = _LABEL_TO_KIND.get(label.lower())
        if kind:
            return kind
    return "Story"
