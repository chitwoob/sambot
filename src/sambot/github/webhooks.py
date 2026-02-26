"""GitHub webhook event handlers."""

from __future__ import annotations

import hashlib
import hmac

import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from sambot.config import get_settings

logger = structlog.get_logger()

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify GitHub webhook signature."""
    if not secret:
        return True  # Skip verification if no secret configured
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(
    request: Request,
    x_hub_signature_256: str = Header(default=""),
    x_github_event: str = Header(default=""),
) -> dict:
    """Handle incoming GitHub webhook events."""
    settings = get_settings()
    payload = await request.body()

    # Verify signature
    if settings.github_webhook_secret and not verify_signature(
        payload, x_hub_signature_256, settings.github_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid signature")

    body = await request.json()

    logger.info("webhook.received", github_event=x_github_event, action=body.get("action"))

    # Route events to handlers
    if x_github_event == "projects_v2_item":
        await handle_project_item_event(body)
    elif x_github_event == "issues":
        await handle_issue_event(body)

    return {"status": "ok"}


async def handle_project_item_event(payload: dict) -> None:
    """Handle project item change events (e.g., status column change)."""
    action = payload.get("action")
    changes = payload.get("changes", {})

    logger.info(
        "webhook.project_item",
        action=action,
        changes=changes,
    )

    # TODO Phase 3: Detect "moved to In Progress" → enqueue agent job


async def handle_issue_event(payload: dict) -> None:
    """Handle issue events (assignment, label changes, etc.)."""
    action = payload.get("action")
    issue = payload.get("issue", {})

    logger.info(
        "webhook.issue",
        action=action,
        issue_number=issue.get("number"),
    )

    # TODO Phase 2: Handle issue assignment → trigger workflow
