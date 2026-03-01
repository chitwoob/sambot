"""Tests for the GitHub poller."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest


@dataclass
class _FakeItem:
    """Minimal stand-in for ProjectItem."""

    item_id: str
    issue_number: int
    title: str
    body: str
    status: str
    labels: list[str]


@pytest.fixture
def _make_poller(monkeypatch):
    """Factory that builds a GitHubPoller with mocked dependencies."""

    def _factory(items: list[_FakeItem], *, on_trigger=None, on_pr_approved=None, trigger_status="Ready"):
        from sambot.config import get_settings

        settings = get_settings()

        github_mock = object()  # not used directly

        projects_mock = AsyncMock()
        projects_mock.get_items = AsyncMock(return_value=items)

        from sambot.github.poller import GitHubPoller

        return GitHubPoller(
            settings,
            github_mock,
            projects_mock,
            on_trigger=on_trigger,
            on_pr_approved=on_pr_approved,
            trigger_status=trigger_status,
        )

    return _factory


@pytest.mark.asyncio
async def test_poller_triggers_ready(_make_poller):
    """Poller fires callback for items with 'Ready' status."""
    triggered = []
    items = [
        _FakeItem("id1", 10, "Feature A", "", "Ready", ["feature"]),
        _FakeItem("id2", 20, "Feature B", "", "Todo", []),
    ]

    poller = _make_poller(items, on_trigger=lambda item: triggered.append(item.issue_number))
    await poller._poll()

    assert triggered == [10]


@pytest.mark.asyncio
async def test_poller_skips_already_seen(_make_poller):
    """Poller does not re-trigger issues it has already dispatched."""
    triggered = []
    items = [_FakeItem("id1", 10, "Feature A", "", "Ready", [])]

    poller = _make_poller(items, on_trigger=lambda item: triggered.append(item.issue_number))

    await poller._poll()
    await poller._poll()  # second poll — should NOT trigger again

    assert triggered == [10]


@pytest.mark.asyncio
async def test_poller_mark_seen(_make_poller):
    """Manually marking an issue as seen prevents triggering."""
    triggered = []
    items = [_FakeItem("id1", 10, "Feature A", "", "Ready", [])]

    poller = _make_poller(items, on_trigger=lambda item: triggered.append(item.issue_number))
    poller.mark_seen(10)
    await poller._poll()

    assert triggered == []


@pytest.mark.asyncio
async def test_poller_seen_issues_property(_make_poller):
    """seen_issues returns a copy of dispatched issue numbers."""
    items = [_FakeItem("id1", 10, "Feature A", "", "Ready", [])]
    poller = _make_poller(items, on_trigger=lambda _: None)

    assert poller.seen_issues == set()
    await poller._poll()
    assert poller.seen_issues == {10}


@pytest.mark.asyncio
async def test_poller_custom_trigger_status(_make_poller):
    """Poller respects a custom trigger status."""
    triggered = []
    items = [
        _FakeItem("id1", 10, "Fix", "", "Ready", []),
        _FakeItem("id2", 20, "Other", "", "In Progress", []),
    ]

    poller = _make_poller(
        items,
        on_trigger=lambda item: triggered.append(item.issue_number),
        trigger_status="Ready",
    )
    await poller._poll()

    assert triggered == [10]


@pytest.mark.asyncio
async def test_poller_no_callback(_make_poller):
    """Poller still tracks seen issues even without a callback."""
    items = [_FakeItem("id1", 10, "Feature A", "", "Ready", [])]
    poller = _make_poller(items)

    await poller._poll()
    assert 10 in poller.seen_issues


@pytest.mark.asyncio
async def test_poller_case_insensitive(_make_poller):
    """Status matching is case-insensitive."""
    triggered = []
    items = [_FakeItem("id1", 10, "Fix", "", "ready", [])]

    poller = _make_poller(items, on_trigger=lambda item: triggered.append(item.issue_number))
    await poller._poll()

    assert triggered == [10]


@pytest.mark.asyncio
async def test_poller_start_stop(_make_poller):
    """Poller.start runs until stop is called."""
    items = []
    poller = _make_poller(items)
    poller._poll_interval = 0.05  # fast loop for test

    async def _stop_after():
        await asyncio.sleep(0.1)
        poller.stop()

    stop_task = asyncio.create_task(_stop_after())
    # start() would loop forever without stop()
    await asyncio.wait_for(poller.start(), timeout=2.0)
    await stop_task
    assert not poller._running


@pytest.mark.asyncio
async def test_poller_callback_error_is_logged(_make_poller):
    """Poller continues even if the callback raises."""
    call_count = 0

    def _bad_callback(item):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    items = [
        _FakeItem("id1", 10, "A", "", "Ready", []),
        _FakeItem("id2", 20, "B", "", "Ready", []),
    ]
    poller = _make_poller(items, on_trigger=_bad_callback)
    await poller._poll()

    # Only the first (highest priority) should be attempted per poll cycle
    assert call_count == 1
    assert 10 in poller.seen_issues


@pytest.mark.asyncio
async def test_poller_picks_one_per_cycle(_make_poller):
    """Poller only picks the highest-priority Ready item per cycle."""
    triggered = []
    items = [
        _FakeItem("id1", 10, "First", "", "Ready", []),
        _FakeItem("id2", 20, "Second", "", "Ready", []),
        _FakeItem("id3", 30, "Third", "", "Ready", []),
    ]

    poller = _make_poller(items, on_trigger=lambda item: triggered.append(item.issue_number))

    # First poll picks item 10
    await poller._poll()
    assert triggered == [10]

    # Second poll picks item 20
    await poller._poll()
    assert triggered == [10, 20]

    # Third poll picks item 30
    await poller._poll()
    assert triggered == [10, 20, 30]


@pytest.mark.asyncio
async def test_poller_recycles_returned_items(_make_poller):
    """Poller re-triggers items that moved out of Ready and came back."""
    triggered = []

    # Poll 1: item 10 is Ready, gets triggered
    items_ready = [_FakeItem("id1", 10, "Feature A", "", "Ready", [])]
    poller = _make_poller(items_ready, on_trigger=lambda item: triggered.append(item.issue_number))
    await poller._poll()
    assert triggered == [10]

    # Poll 2: item 10 moved to In Progress (worker picked it up)
    items_in_progress = [_FakeItem("id1", 10, "Feature A", "", "In progress", [])]
    poller._projects.get_items.return_value = items_in_progress
    await poller._poll()
    assert triggered == [10]  # no new trigger

    # Poll 3: item 10 returned to Ready (worker crashed)
    poller._projects.get_items.return_value = items_ready
    await poller._poll()
    assert triggered == [10, 10]  # re-triggered!


@pytest.mark.asyncio
async def test_poller_does_not_recycle_without_leaving(_make_poller):
    """Poller does NOT re-trigger items that stay in Ready (no round-trip)."""
    triggered = []
    items = [_FakeItem("id1", 10, "Feature A", "", "Ready", [])]
    poller = _make_poller(items, on_trigger=lambda item: triggered.append(item.issue_number))

    await poller._poll()
    assert triggered == [10]

    # Item stays in Ready — should NOT be re-triggered
    await poller._poll()
    assert triggered == [10]
