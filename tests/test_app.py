"""Tests for the FastAPI app."""

from __future__ import annotations


def test_health_check(client):
    """Health endpoint returns 200 with version."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


def test_webhook_endpoint_exists(client):
    """Webhook endpoint accepts POST requests."""
    response = client.post(
        "/webhooks/github",
        json={"action": "test"},
        headers={"X-GitHub-Event": "ping"},
    )
    assert response.status_code == 200
