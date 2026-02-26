"""Application configuration via environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All SamBot configuration, loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # --- GitHub ---
    github_token: str = Field(description="GitHub personal access token")
    github_repo: str = Field(description="Target repo in owner/repo format")
    github_project_number: int = Field(default=1, description="GitHub Projects V2 project number")

    # --- Anthropic ---
    anthropic_api_key: str = Field(description="Anthropic API key")

    # --- Slack ---
    slack_bot_token: str = Field(default="", description="Slack bot OAuth token (xoxb-)")
    slack_app_token: str = Field(default="", description="Slack app-level token (xapp-)")
    slack_signing_secret: str = Field(default="", description="Slack signing secret")
    slack_progress_channel: str = Field(default="sambot-progress", description="Slack channel for progress updates")
    slack_questions_channel: str = Field(default="sambot-questions", description="Slack channel for agent Q&A")
    slack_backlog_channel: str = Field(default="sambot-backlog", description="Slack channel for backlog story building")

    # --- Redis ---
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")

    # --- App ---
    sambot_log_level: str = Field(default="INFO", description="Log level")
    sambot_work_dir: Path = Field(default=Path("/tmp/sambot-workspaces"), description="Working directory for cloned repos")
    sambot_max_agent_passes: int = Field(default=5, description="Maximum coding passes per story")
    sambot_question_timeout_minutes: int = Field(default=30, description="Minutes to wait for Slack Q&A response")
    sambot_base_branch: str = Field(default="develop", description="Base branch for PRs")
    sambot_poll_interval: int = Field(default=30, description="Seconds between GitHub polling cycles")
    sambot_memory_max_tokens: int = Field(default=2000, description="Soft token limit for agent memory (approx 4 chars/token)")

    @property
    def github_owner(self) -> str:
        return self.github_repo.split("/")[0]

    @property
    def github_repo_name(self) -> str:
        return self.github_repo.split("/")[1]


def get_settings() -> Settings:
    """Create and return settings instance."""
    return Settings()  # type: ignore[call-arg]
