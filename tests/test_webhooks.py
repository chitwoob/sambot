"""Tests for webhook handling."""

from __future__ import annotations


def test_webhook_ping(client):
    """Ping events return 200."""
    response = client.post(
        "/webhooks/github",
        json={"action": "ping"},
        headers={"X-GitHub-Event": "ping"},
    )
    assert response.status_code == 200


def test_webhook_project_item_event(client):
    """Project item events are accepted."""
    response = client.post(
        "/webhooks/github",
        json={
            "action": "edited",
            "changes": {"field_value": {"field_name": "Status"}},
        },
        headers={"X-GitHub-Event": "projects_v2_item"},
    )
    assert response.status_code == 200


def test_webhook_issue_event(client):
    """Issue events are accepted."""
    response = client.post(
        "/webhooks/github",
        json={
            "action": "assigned",
            "issue": {"number": 42, "title": "Test issue"},
        },
        headers={"X-GitHub-Event": "issues"},
    )
    assert response.status_code == 200


def test_webhook_signature_verification(client, monkeypatch):
    """Webhook rejects requests with invalid signatures when secret is set."""
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")

    response = client.post(
        "/webhooks/github",
        json={"action": "ping"},
        headers={
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert response.status_code == 401
