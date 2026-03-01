"""Tests for GitHub Projects V2 client — draft conversion and item parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from sambot.github.projects import ProjectsClient


def _make_graphql_response(items_nodes, project_title="Test Project"):
    """Build a fake GraphQL response for QUERY_PROJECT_ITEMS."""
    return {
        "user": {
            "projectV2": {
                "id": "PVT_proj1",
                "title": project_title,
                "items": {"nodes": items_nodes},
            }
        }
    }


def _issue_node(item_id, number, title, status, *, body="", labels=None):
    """Build a project node with Issue content."""
    return {
        "id": item_id,
        "fieldValues": {
            "nodes": [
                {"field": {"name": "Status"}, "name": status},
            ]
        },
        "content": {
            "__typename": "Issue",
            "number": number,
            "title": title,
            "body": body,
            "state": "OPEN",
            "labels": {"nodes": [{"name": l} for l in (labels or [])]},
        },
    }


def _draft_node(item_id, title, status, *, body=""):
    """Build a project node with DraftIssue content."""
    return {
        "id": item_id,
        "fieldValues": {
            "nodes": [
                {"field": {"name": "Status"}, "name": status},
            ]
        },
        "content": {
            "__typename": "DraftIssue",
            "title": title,
            "body": body,
        },
    }


@pytest.fixture
def _github_mock():
    """Create a mock GitHubClient."""
    mock = AsyncMock()
    return mock


@pytest.fixture
def _client(_github_mock):
    """Create a ProjectsClient with mocked GitHub client."""
    return ProjectsClient(_github_mock, owner="testuser", repo="testrepo", project_number=1)


@pytest.mark.asyncio
async def test_get_items_returns_issues(_github_mock, _client):
    """get_items parses Issue nodes correctly."""
    nodes = [
        _issue_node("id1", 10, "Feature A", "Ready", labels=["bug"]),
        _issue_node("id2", 20, "Feature B", "Done"),
    ]
    _github_mock.graphql = AsyncMock(return_value=_make_graphql_response(nodes))

    items = await _client.get_items()

    assert len(items) == 2
    assert items[0].issue_number == 10
    assert items[0].status == "Ready"
    assert items[0].labels == ["bug"]
    assert items[1].issue_number == 20


@pytest.mark.asyncio
async def test_get_items_converts_drafts(_github_mock, _client):
    """get_items auto-converts DraftIssue nodes to real Issues."""
    nodes = [
        _issue_node("id1", 10, "Regular Issue", "Done"),
        _draft_node("draft1", "My Draft", "Ready", body="draft body"),
    ]
    _github_mock.graphql = AsyncMock(
        side_effect=[
            # First call: QUERY_PROJECT_ITEMS
            _make_graphql_response(nodes),
            # Second call: QUERY_REPO_ID
            {"repository": {"id": "R_repo1"}},
            # Third call: MUTATION_CONVERT_DRAFT
            {
                "convertProjectV2DraftIssueItemToIssue": {
                    "item": {
                        "id": "id_converted",
                        "content": {
                            "number": 35,
                            "title": "My Draft",
                            "body": "draft body",
                            "state": "OPEN",
                            "labels": {"nodes": []},
                        },
                    }
                }
            },
        ]
    )

    items = await _client.get_items()

    assert len(items) == 2
    # The regular issue
    assert items[0].issue_number == 10
    # The converted draft
    assert items[1].issue_number == 35
    assert items[1].title == "My Draft"
    assert items[1].status == "Ready"


@pytest.mark.asyncio
async def test_get_items_draft_conversion_failure_continues(_github_mock, _client):
    """get_items continues if a draft conversion fails."""
    nodes = [
        _issue_node("id1", 10, "Regular", "Done"),
        _draft_node("draft1", "Bad Draft", "Ready"),
    ]
    _github_mock.graphql = AsyncMock(
        side_effect=[
            _make_graphql_response(nodes),
            {"repository": {"id": "R_repo1"}},
            RuntimeError("conversion failed"),
        ]
    )

    items = await _client.get_items()

    # Only the regular issue survives
    assert len(items) == 1
    assert items[0].issue_number == 10


@pytest.mark.asyncio
async def test_convert_draft_to_issue(_github_mock, _client):
    """convert_draft_to_issue calls the mutation and returns the result."""
    _client._project_id = "PVT_proj1"
    _github_mock.graphql = AsyncMock(
        side_effect=[
            # QUERY_REPO_ID
            {"repository": {"id": "R_repo1"}},
            # MUTATION_CONVERT_DRAFT
            {
                "convertProjectV2DraftIssueItemToIssue": {
                    "item": {
                        "id": "id_new",
                        "content": {
                            "number": 42,
                            "title": "Converted",
                            "body": "",
                            "labels": {"nodes": []},
                        },
                    }
                }
            },
        ]
    )

    result = await _client.convert_draft_to_issue("draft_item_1")

    assert result is not None
    assert result["item"]["content"]["number"] == 42
    # Verify the mutation was called with correct args
    call_args = _github_mock.graphql.call_args_list[1]
    assert call_args[0][1]["itemId"] == "draft_item_1"
    assert call_args[0][1]["repositoryId"] == "R_repo1"


@pytest.mark.asyncio
async def test_convert_draft_no_project_id(_github_mock, _client):
    """convert_draft_to_issue returns None when project_id is unknown."""
    assert _client._project_id is None

    result = await _client.convert_draft_to_issue("draft1")

    assert result is None
    _github_mock.graphql.assert_not_called()


@pytest.mark.asyncio
async def test_repo_id_cached(_github_mock, _client):
    """_ensure_repo_id caches the result after the first call."""
    _github_mock.graphql = AsyncMock(return_value={"repository": {"id": "R_cached"}})

    rid1 = await _client._ensure_repo_id()
    rid2 = await _client._ensure_repo_id()

    assert rid1 == rid2 == "R_cached"
    # Only one GraphQL call — second was cached
    assert _github_mock.graphql.call_count == 1


@pytest.mark.asyncio
async def test_extract_status():
    """_extract_status pulls the Status field value."""
    node = {
        "fieldValues": {
            "nodes": [
                {"field": {"name": "Title"}, "text": "Something"},
                {"field": {"name": "Status"}, "name": "In progress"},
            ]
        }
    }
    assert ProjectsClient._extract_status(node) == "In progress"


@pytest.mark.asyncio
async def test_extract_status_missing():
    """_extract_status returns empty string when Status field absent."""
    node = {"fieldValues": {"nodes": [{"field": {"name": "Title"}, "text": "X"}]}}
    assert ProjectsClient._extract_status(node) == ""


@pytest.mark.asyncio
async def test_recover_interrupted_jobs_moves_orphans_to_ready(monkeypatch):
    """Startup recovery moves orphaned In-progress items back to Ready."""
    from unittest.mock import MagicMock, patch

    from sambot.main import _recover_interrupted_jobs

    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"

    projects = AsyncMock()
    # One item in progress, one in Ready
    projects.get_items = AsyncMock(return_value=[
        MagicMock(item_id="id1", issue_number=38, title="Stuck", status="In progress"),
        MagicMock(item_id="id2", issue_number=39, title="Waiting", status="Ready"),
    ])
    projects.update_status = AsyncMock()

    # Mock Redis/RQ — empty queue (no active jobs)
    mock_queue = MagicMock()
    mock_queue.jobs = []
    mock_queue.started_job_registry.get_job_ids.return_value = []

    with patch("redis.Redis") as mock_redis_cls, \
         patch("rq.Queue", return_value=mock_queue):
        mock_redis_cls.from_url.return_value = MagicMock()

        await _recover_interrupted_jobs(settings, projects)

    # Should only move #38 (In progress) back to Ready
    projects.update_status.assert_called_once_with("id1", "Ready")


@pytest.mark.asyncio
async def test_recover_skips_active_jobs(monkeypatch):
    """Startup recovery does not touch items with active RQ jobs."""
    from unittest.mock import MagicMock, patch

    from sambot.main import _recover_interrupted_jobs

    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"

    projects = AsyncMock()
    projects.get_items = AsyncMock(return_value=[
        MagicMock(item_id="id1", issue_number=38, title="Active", status="In progress"),
    ])
    projects.update_status = AsyncMock()

    # Mock RQ queue with an active job for issue 38
    mock_job = MagicMock()
    mock_job.func_name = "sambot.jobs.worker.process_story"
    mock_job.args = (38,)
    mock_queue = MagicMock()
    mock_queue.jobs = [mock_job]
    mock_queue.started_job_registry.get_job_ids.return_value = []

    with patch("redis.Redis") as mock_redis_cls, \
         patch("rq.Queue", return_value=mock_queue):
        mock_redis_cls.from_url.return_value = MagicMock()

        await _recover_interrupted_jobs(settings, projects)

    # Should NOT move — job is still active
    projects.update_status.assert_not_called()
