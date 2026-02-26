"""Tests for configuration."""

from __future__ import annotations


def test_settings_load():
    """Settings load from environment variables."""
    from sambot.config import get_settings

    settings = get_settings()
    assert settings.github_token == "ghp_test_token"
    assert settings.github_repo == "test-owner/test-repo"
    assert settings.github_owner == "test-owner"
    assert settings.github_repo_name == "test-repo"
    assert settings.github_project_number == 1


def test_settings_defaults():
    """Settings have sensible defaults."""
    from sambot.config import get_settings

    settings = get_settings()
    assert settings.sambot_log_level == "INFO"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.slack_progress_channel == "sambot-progress"
    assert settings.slack_questions_channel == "sambot-questions"
    assert settings.slack_backlog_channel == "sambot-backlog"


def test_settings_agent_defaults():
    """Agent-related settings have correct defaults."""
    from sambot.config import get_settings

    settings = get_settings()
    assert settings.sambot_max_agent_passes == 5
    assert settings.sambot_question_timeout_minutes == 30
    assert settings.sambot_base_branch == "develop"
    assert settings.sambot_memory_max_tokens == 2000


def test_settings_custom_agent_values(monkeypatch):
    """Agent settings can be overridden via env vars."""
    monkeypatch.setenv("SAMBOT_MAX_AGENT_PASSES", "10")
    monkeypatch.setenv("SAMBOT_QUESTION_TIMEOUT_MINUTES", "60")
    monkeypatch.setenv("SAMBOT_BASE_BRANCH", "main")

    from sambot.config import Settings

    settings = Settings()  # type: ignore[call-arg]
    assert settings.sambot_max_agent_passes == 10
    assert settings.sambot_question_timeout_minutes == 60
    assert settings.sambot_base_branch == "main"
