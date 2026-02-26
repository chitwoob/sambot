"""FastAPI application entry point."""

from __future__ import annotations

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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application startup and shutdown."""
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

    yield

    logger.info("sambot.shutdown")


app = FastAPI(
    title="SamBot",
    description="SDLC workflow automation",
    version="0.1.0",
    lifespan=lifespan,
)

# Register routers
from sambot.github.webhooks import router as webhooks_router  # noqa: E402

app.include_router(webhooks_router)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "version": "0.1.0"}
