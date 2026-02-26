"""Shared test fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch):
    """Set required env vars for tests."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    monkeypatch.setenv("GITHUB_REPO", "test-owner/test-repo")
    monkeypatch.setenv("GITHUB_PROJECT_NUMBER", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("SAMBOT_WORK_DIR", "/tmp/sambot-test")


@pytest.fixture
def client():
    """FastAPI test client."""
    from sambot.main import app

    return TestClient(app)
