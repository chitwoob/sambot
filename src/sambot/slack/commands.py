"""Slack slash commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from slack_bolt import App

logger = structlog.get_logger()


def register_commands(app: App) -> None:
    """Register slash command handlers with the Slack app."""

    @app.command("/sambot")
    def handle_sambot_command(ack, command, respond):
        """Handle /sambot slash commands."""
        ack()

        subcommand = command.get("text", "").strip().split()[0] if command.get("text") else "help"
        args = command.get("text", "").strip().split()[1:] if command.get("text") else []

        logger.info("slack.command", subcommand=subcommand, args=args)

        if subcommand == "help":
            respond(_help_text())
        elif subcommand == "status":
            respond("📊 Status check coming in Phase 4")
        elif subcommand == "create":
            respond("📝 Ticket creation coming in Phase 4")
        elif subcommand == "start":
            if not args:
                respond("❌ Usage: `/sambot start <issue-number>`")
            else:
                respond(f"🚀 Starting work on issue #{args[0]} — coming in Phase 3")
        else:
            respond(f"❓ Unknown command: `{subcommand}`. Try `/sambot help`")


def _help_text() -> str:
    return (
        "*SamBot Commands:*\n"
        "• `/sambot help` — Show this help\n"
        "• `/sambot status` — Show current job status\n"
        "• `/sambot create` — Create a new ticket\n"
        "• `/sambot start <issue>` — Start working on an issue\n"
    )
