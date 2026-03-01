"""Database setup and session management."""

from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

engine = None


def init_db(db_path: str | None = None) -> None:
    """Create all tables.

    Args:
        db_path: Optional path to the SQLite database file.
                 When None, uses the settings data dir.
    """
    global engine  # noqa: PLW0603
    if db_path is None:
        from sambot.config import get_settings
        settings = get_settings()
        db_path = str(settings.database_path)
    url = f"sqlite:///{db_path}"
    engine = create_engine(url, echo=False)
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    """Get a database session."""
    if engine is None:
        raise RuntimeError("Database not initialized — call init_db() first")
    return Session(engine)
