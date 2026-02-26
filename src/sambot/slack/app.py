"""Slack Bolt app setup."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

if TYPE_CHECKING:
    from sambot.config import Settings

logger = structlog.get_logger()


def create_slack_app(settings: Settings) -> App | None:
    """Create and configure the Slack Bolt app. Returns None if not configured."""
    if not settings.slack_bot_token or not settings.slack_app_token:
        logger.warning("slack.not_configured", msg="Slack tokens not set, Slack integration disabled")
        return None

    app = App(
        token=settings.slack_bot_token,
        signing_secret=settings.slack_signing_secret,
    )

    # Register handlers (imported here to avoid circular imports)
    from sambot.slack.commands import register_commands

    register_commands(app)

    logger.info("slack.app_created")
    return app


def start_socket_mode(app: App, settings: Settings) -> SocketModeHandler:
    """Start Slack app in socket mode (no public URL needed)."""
    handler = SocketModeHandler(app, settings.slack_app_token)
    handler.start()
    return handler
