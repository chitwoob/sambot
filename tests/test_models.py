"""Tests for database models."""

from __future__ import annotations


def test_story_job_defaults():
    """StoryJob model has correct defaults."""
    from sambot.models import JobStatus, StoryJob

    job = StoryJob(issue_number=42, issue_title="Test story")
    assert job.issue_number == 42
    assert job.status == JobStatus.PENDING
    assert job.branch_name == ""
    assert job.pr_number is None
    assert job.agent_output == ""
    assert job.files_changed == ""
    assert job.passes_used == 0


def test_story_job_asking_status():
    """StoryJob supports the ASKING status for Slack Q&A."""
    from sambot.models import JobStatus, StoryJob

    job = StoryJob(issue_number=1, issue_title="Q&A test", status=JobStatus.ASKING)
    assert job.status == JobStatus.ASKING
    assert job.status.value == "asking"


def test_agent_question_defaults():
    """AgentQuestion model has correct defaults."""
    from sambot.models import AgentQuestion

    q = AgentQuestion(job_id=1, question="What auth provider?")
    assert q.job_id == 1
    assert q.question == "What auth provider?"
    assert q.context == ""
    assert q.answer == ""
    assert q.slack_thread_ts == ""
    assert q.answered_at is None


def test_job_status_values():
    """All expected job statuses exist."""
    from sambot.models import JobStatus

    expected = {"pending", "running", "asking", "success", "failed", "cancelled"}
    actual = {s.value for s in JobStatus}
    assert actual == expected


def test_docker_permission_defaults():
    """DockerPermission model has correct defaults."""
    from sambot.models import DockerPermission

    perm = DockerPermission(
        repo="owner/repo",
        file_path="Dockerfile",
    )
    assert perm.repo == "owner/repo"
    assert perm.file_path == "Dockerfile"
    assert perm.file_hash == ""
    assert perm.approved is False
    assert perm.approved_by == ""
    assert perm.approved_at is None


def test_docker_permission_approved():
    """DockerPermission tracks approval state."""
    from datetime import UTC, datetime
    from sambot.models import DockerPermission

    now = datetime.now(UTC)
    perm = DockerPermission(
        repo="owner/repo",
        file_path="docker-compose.yml",
        approved=True,
        approved_by="U12345",
        approved_at=now,
    )
    assert perm.approved is True
    assert perm.approved_by == "U12345"
    assert perm.approved_at == now
