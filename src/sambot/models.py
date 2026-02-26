"""Database models for SamBot."""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class JobStatus(enum.StrEnum):
    """Status of an agent job."""

    PENDING = "pending"
    RUNNING = "running"
    ASKING = "asking"  # Agent is waiting for a Slack answer
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StoryJob(SQLModel, table=True):
    """Tracks an agent job for a GitHub issue/story."""

    id: int | None = Field(default=None, primary_key=True)
    issue_number: int = Field(index=True)
    issue_title: str
    branch_name: str = Field(default="")
    pr_number: int | None = Field(default=None)
    status: JobStatus = Field(default=JobStatus.PENDING)
    agent_output: str = Field(default="")
    files_changed: str = Field(default="")  # Comma-separated list
    passes_used: int = Field(default=0)
    error_message: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = Field(default=None)


class AgentQuestion(SQLModel, table=True):
    """Tracks questions the agent asked during a job."""

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    question: str
    context: str = Field(default="")
    answer: str = Field(default="")
    slack_thread_ts: str = Field(default="")
    asked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    answered_at: datetime | None = Field(default=None)
