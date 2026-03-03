"""Shared logging configuration for both the API and the RQ worker processes.

Call ``configure_logging(settings)`` once per process on startup.  It:
- Routes structlog through Python's stdlib logging (so handler levels apply)
- Writes DEBUG and above to ``<data_dir>/debug.log`` (rotating, 50 MB × 3)
- Writes INFO and above to stdout (keeps docker logs readable)
"""

from __future__ import annotations

import logging
import logging.handlers
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sambot.config import Settings

_configured = False


def configure_logging(settings: Settings, log_filename: str = "debug.log") -> None:
    """Configure stdlib logging + structlog for the current process.

    Each process should pass a unique *log_filename* so they never write
    to the same file concurrently (e.g. ``api.log`` vs ``worker.log``).

    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _configured  # noqa: PLW0603
    if _configured:
        return
    _configured = True

    settings.sambot_data_dir.mkdir(parents=True, exist_ok=True)

    log_path = settings.sambot_data_dir / log_filename
    file_handler = logging.handlers.RotatingFileHandler(
        log_path,
        maxBytes=50 * 1024 * 1024,  # 50 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s")
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # root must be DEBUG; handlers filter individually
    root.handlers.clear()         # remove any handlers from earlier basicConfig calls
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,  # must be False so worker picks up config
    )
