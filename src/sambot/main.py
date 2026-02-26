"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI

from sambot.config import get_settings
from sambot.db import init_db

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger()

# Module-level reference so other parts of the app can stop it if needed
_poller_task: asyncio.Task | None = None


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

    # Initialize database
    init_db()
    logger.info("sambot.db_initialized")

    # Ensure work directory exists
    settings.sambot_work_dir.mkdir(parents=True, exist_ok=True)

    # Start GitHub poller as a background task
    from sambot.github.client import GitHubClient
    from sambot.github.poller import GitHubPoller
    from sambot.github.projects import ProjectsClient

    github = GitHubClient(settings)
    projects = ProjectsClient(
        github,
        owner=settings.github_owner,
        repo=settings.github_repo_name,
        project_number=settings.github_project_number,
    )

    def _on_trigger(item):
        """Callback when a story moves to In Progress."""
        logger.info("poller.dispatch", issue_number=item.issue_number, title=item.title)
        # TODO Phase 3: enqueue process_story job via RQ
        # from rq import Queue
        # queue = Queue(connection=redis_conn)
        # queue.enqueue(process_story, item.issue_number)

    poller = GitHubPoller(settings, github, projects, on_trigger=_on_trigger)
    _poller_task = asyncio.create_task(poller.start())
    logger.info("sambot.poller_started", interval=settings.sambot_poll_interval)

    yield

    # Shutdown
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
