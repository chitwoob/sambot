"""Tests for the agent module — tools, test runner, memory."""

from __future__ import annotations

import textwrap
from pathlib import Path

# ---------- ToolExecutor tests ----------


def test_tool_executor_read_file(tmp_path: Path):
    """ToolExecutor reads files correctly."""
    from sambot.agent.tools import ToolExecutor

    (tmp_path / "hello.txt").write_text("Hello, world!")
    executor = ToolExecutor(tmp_path)
    result = executor.read_file("hello.txt")
    assert result.success is True
    assert result.output == "Hello, world!"


def test_tool_executor_read_file_not_found(tmp_path: Path):
    """ToolExecutor returns error for missing file."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    result = executor.read_file("missing.txt")
    assert result.success is False
    assert "not found" in result.output.lower()


def test_tool_executor_write_file(tmp_path: Path):
    """ToolExecutor writes files and creates directories."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    result = executor.write_file("sub/dir/new.py", "print('hi')")
    assert result.success is True
    assert (tmp_path / "sub" / "dir" / "new.py").read_text() == "print('hi')"


def test_tool_executor_list_directory(tmp_path: Path):
    """ToolExecutor lists directories, marking subdirs with /."""
    from sambot.agent.tools import ToolExecutor

    (tmp_path / "file.txt").write_text("content")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "inner.py").write_text("")
    executor = ToolExecutor(tmp_path)
    result = executor.list_directory(".")
    assert result.success is True
    assert "file.txt" in result.output
    assert "subdir/" in result.output


def test_tool_executor_list_empty_directory(tmp_path: Path):
    """ToolExecutor handles empty directories."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    result = executor.list_directory(".")
    assert result.success is True
    assert "empty" in result.output.lower()


def test_tool_executor_path_traversal(tmp_path: Path):
    """ToolExecutor blocks path traversal attempts."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    result = executor.read_file("../../etc/passwd")
    assert result.success is False
    assert "traversal" in result.output.lower() or "error" in result.output.lower()


def test_tool_executor_execute_dispatch(tmp_path: Path):
    """ToolExecutor.execute dispatches to the right method."""
    from sambot.agent.tools import ToolExecutor

    (tmp_path / "data.txt").write_text("payload")
    executor = ToolExecutor(tmp_path)

    result = executor.execute("read_file", {"path": "data.txt"})
    assert result.success is True
    assert result.output == "payload"

    result = executor.execute("unknown_tool", {})
    assert result.success is False
    assert "unknown" in result.output.lower()


def test_tool_executor_skips_hidden_and_pycache(tmp_path: Path):
    """ToolExecutor.list_directory skips hidden files and __pycache__."""
    from sambot.agent.tools import ToolExecutor

    (tmp_path / ".hidden").write_text("")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "visible.py").write_text("")
    executor = ToolExecutor(tmp_path)
    result = executor.list_directory(".")
    assert "visible.py" in result.output
    assert ".hidden" not in result.output
    assert "__pycache__" not in result.output


# ---------- Tool definitions tests ----------


def test_tool_definitions_names():
    """All expected tools are defined."""
    from sambot.agent.tools import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {
        "read_file", "write_file", "list_directory", "run_tests",
        "ask_question", "search_files", "grep_file", "run_command",
        "request_docker_permission",
    }


def test_tool_definitions_have_schemas():
    """Every tool definition has a proper input_schema."""
    from sambot.agent.tools import TOOL_DEFINITIONS

    for tool in TOOL_DEFINITIONS:
        assert "input_schema" in tool
        assert tool["input_schema"]["type"] == "object"
        assert "properties" in tool["input_schema"]


# ---------- TestRunner / TestResult tests ----------


def test_test_result_summary():
    """TestResult.summary formats correctly."""
    from sambot.agent.test_runner import TestResult

    result = TestResult(success=True, exit_code=0, output="", passed=5, failed=0, errors=0, total=5)
    assert "PASSED" in result.summary
    assert "5 passed" in result.summary

    result = TestResult(success=False, exit_code=1, output="", passed=3, failed=2, errors=0, total=5)
    assert "FAILED" in result.summary
    assert "2 failed" in result.summary


def test_test_runner_parse_output():
    """TestRunner parses pytest summary lines."""
    from sambot.agent.test_runner import TestRunner

    runner = TestRunner(Path("/tmp"))

    output = textwrap.dedent("""\
        tests/test_foo.py::test_one PASSED
        tests/test_foo.py::test_two FAILED
        FAILED tests/test_foo.py::test_two - AssertionError
        ===== 1 passed, 1 failed in 0.5s =====
    """)

    result = runner._parse_output(output, exit_code=1)
    assert result.passed == 1
    assert result.failed == 1
    assert result.success is False
    assert len(result.failure_details) == 1


def test_test_runner_parse_all_pass():
    """TestRunner parses a fully passing result."""
    from sambot.agent.test_runner import TestRunner

    runner = TestRunner(Path("/tmp"))
    output = "===== 5 passed in 1.2s =====\n"
    result = runner._parse_output(output, exit_code=0)
    assert result.passed == 5
    assert result.failed == 0
    assert result.success is True
    assert result.total == 5


# ---------- MemoryManager tests ----------


def test_memory_manager_load_missing(tmp_path: Path):
    """MemoryManager.load returns empty string when file is missing."""
    from sambot.agent.memory import MemoryManager

    mgr = MemoryManager(memory_path=tmp_path / "nonexistent.md")
    assert mgr.load() == ""


def test_memory_manager_load_existing(tmp_path: Path):
    """MemoryManager.load reads existing memory file."""
    from sambot.agent.memory import MemoryManager

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Memory\nSome facts.")
    mgr = MemoryManager(memory_path=mem_file)
    content = mgr.load()
    assert "Some facts." in content


def test_memory_manager_save(tmp_path: Path):
    """MemoryManager.save writes content to disk."""
    from sambot.agent.memory import MemoryManager

    mem_file = tmp_path / "MEMORY.md"
    mgr = MemoryManager(memory_path=mem_file)
    mgr.save("# Memory\nUpdated facts.")
    assert mem_file.read_text() == "# Memory\nUpdated facts."


def test_memory_manager_build_story_context(tmp_path: Path):
    """build_story_context combines memory and story info."""
    from sambot.agent.memory import MemoryManager

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("# Stack\nPython + FastAPI")
    mgr = MemoryManager(memory_path=mem_file)

    context = mgr.build_story_context(
        story_title="Add login",
        story_body="Implement OAuth2 login flow.",
        labels=["feature", "auth"],
    )

    assert "Project Memory" in context
    assert "Python + FastAPI" in context
    assert "Add login" in context
    assert "OAuth2 login flow" in context
    assert "feature" in context
    assert "auth" in context


def test_memory_manager_build_story_context_no_memory(tmp_path: Path):
    """build_story_context works without existing memory."""
    from sambot.agent.memory import MemoryManager

    mgr = MemoryManager(memory_path=tmp_path / "missing.md")
    context = mgr.build_story_context(story_title="Fix bug", story_body="Button broken")

    assert "Fix bug" in context
    assert "Button broken" in context
    # No memory section when file doesn't exist
    assert "Project Memory" not in context


def test_memory_manager_build_story_context_no_labels(tmp_path: Path):
    """build_story_context omits labels section when none given."""
    from sambot.agent.memory import MemoryManager

    mem_file = tmp_path / "MEMORY.md"
    mem_file.write_text("mem content")
    mgr = MemoryManager(memory_path=mem_file)
    context = mgr.build_story_context(story_title="Story", story_body="Body")
    assert "Labels" not in context


# ---------- MemoryManager budget tests ----------


def test_memory_manager_token_budget(tmp_path: Path):
    """MemoryManager exposes max_tokens and max_chars."""
    from sambot.agent.memory import MemoryManager

    mgr = MemoryManager(memory_path=tmp_path / "m.md", max_tokens=1000)
    assert mgr.max_tokens == 1000
    assert mgr.max_chars == 4000  # 4 chars per token


def test_memory_manager_is_over_budget(tmp_path: Path):
    """is_over_budget detects when memory exceeds the soft limit."""
    from sambot.agent.memory import MemoryManager

    mem_file = tmp_path / "m.md"
    # 500 tokens * 4 chars = 2000 chars budget
    mgr = MemoryManager(memory_path=mem_file, max_tokens=500)

    # Under budget
    mem_file.write_text("x" * 1999)
    assert mgr.is_over_budget() is False

    # Over budget
    mem_file.write_text("x" * 2001)
    assert mgr.is_over_budget() is True


def test_memory_manager_default_budget(tmp_path: Path):
    """Default token budget is 2000."""
    from sambot.agent.memory import MemoryManager

    mgr = MemoryManager(memory_path=tmp_path / "m.md")
    assert mgr.max_tokens == 2000


def test_memory_manager_save_creates_dirs(tmp_path: Path):
    """MemoryManager.save creates parent directories."""
    from sambot.agent.memory import MemoryManager

    deep_path = tmp_path / "a" / "b" / "mem.md"
    mgr = MemoryManager(memory_path=deep_path)
    mgr.save("deep content")
    assert deep_path.read_text() == "deep content"


# ---------- BacklogAgent parser tests ----------


def test_backlog_parse_story_response():
    """BacklogAgent._parse_story_response parses structured output."""
    from sambot.agent.backlog import BacklogAgent

    response = (
        "TITLE: Add user authentication\n\n"
        "DESCRIPTION:\nImplement JWT-based auth flow.\n\n"
        "ACCEPTANCE CRITERIA:\n"
        "- Users can sign up with email/password\n"
        "- Login returns a JWT token\n"
        "- Protected routes require valid token\n\n"
        "LABELS: feature, auth\n\n"
        "FOLLOW-UP QUESTIONS: none"
    )

    parsed = BacklogAgent._parse_story_response(response)
    assert parsed["title"] == "Add user authentication"
    assert "JWT-based auth" in parsed["description"]
    assert len(parsed["acceptance_criteria"]) == 3
    assert "feature" in parsed["labels"]
    assert "auth" in parsed["labels"]
    assert parsed["follow_up_questions"] == []


def test_backlog_parse_with_questions():
    """Parser extracts follow-up questions."""
    from sambot.agent.backlog import BacklogAgent

    response = (
        "TITLE: Fix checkout bug\n\n"
        "DESCRIPTION:\nCart total is incorrect.\n\n"
        "ACCEPTANCE CRITERIA:\n"
        "- Total matches sum of items\n\n"
        "LABELS: bug\n\n"
        "FOLLOW-UP QUESTIONS:\n"
        "- Does this include tax calculations?\n"
        "- Which payment providers are affected?"
    )

    parsed = BacklogAgent._parse_story_response(response)
    assert parsed["title"] == "Fix checkout bug"
    assert len(parsed["follow_up_questions"]) == 2
    assert "tax" in parsed["follow_up_questions"][0]


# ---------- System prompts tests ----------


def test_build_system_prompt_with_memory():
    """build_system_prompt injects memory into the preamble."""
    from sambot.llm.prompts import build_system_prompt

    result = build_system_prompt("Agent instructions here.", "KEY FACT: use Python 3.12+")
    assert "KEY FACT: use Python 3.12+" in result
    assert "Agent instructions here." in result
    assert "Project Memory" in result.lower() or "project memory" in result


def test_build_system_prompt_without_memory():
    """build_system_prompt works without memory."""
    from sambot.llm.prompts import build_system_prompt

    result = build_system_prompt("Do the thing.")
    assert "Do the thing." in result
    assert "No project memory available" in result


def test_all_agent_prompts_exist():
    """All expected agent system prompts are defined."""
    from sambot.llm import prompts

    assert hasattr(prompts, "CODING_AGENT_SYSTEM")
    assert hasattr(prompts, "BACKLOG_AGENT_SYSTEM")
    assert hasattr(prompts, "STORY_REFINEMENT_SYSTEM")
    assert hasattr(prompts, "PR_DESCRIPTION_SYSTEM")
    assert hasattr(prompts, "MEMORY_COMPRESSION_SYSTEM")


# ---------- BacklogAgent intent classification tests ----------


def test_backlog_classify_intent_create():
    """classify_intent returns 'create' for confirmation messages."""
    from unittest.mock import MagicMock

    from sambot.agent.backlog import BacklogAgent

    llm = MagicMock()
    llm.complete_raw.return_value = "CREATE"
    settings = MagicMock()
    settings.sambot_memory_max_tokens = 2000
    settings.backlog_memory_path = "/tmp/test_backlog_memory.md"

    agent = BacklogAgent(llm_client=llm, settings=settings, memory_path=Path("/tmp/test_bl.md"))
    intent = agent.classify_intent("Let's create the ticket.", conversation_context="prior messages")
    assert intent == "create"
    llm.complete_raw.assert_called_once()


def test_backlog_classify_intent_refine():
    """classify_intent returns 'refine' for informational messages."""
    from unittest.mock import MagicMock

    from sambot.agent.backlog import BacklogAgent

    llm = MagicMock()
    llm.complete_raw.return_value = "REFINE"
    settings = MagicMock()
    settings.sambot_memory_max_tokens = 2000
    settings.backlog_memory_path = "/tmp/test_backlog_memory.md"

    agent = BacklogAgent(llm_client=llm, settings=settings, memory_path=Path("/tmp/test_bl.md"))
    intent = agent.classify_intent("Actually, we also need dark mode support.")
    assert intent == "refine"


def test_backlog_create_backlog_item():
    """create_backlog_item creates a draft issue on the project board."""
    from unittest.mock import MagicMock, patch

    from sambot.agent.backlog import BacklogAgent

    llm = MagicMock()
    settings = MagicMock()
    settings.sambot_memory_max_tokens = 2000
    settings.backlog_memory_path = "/tmp/test_backlog_memory.md"
    settings.resolved_project_owner = "test-owner"
    settings.github_project_number = 1

    agent = BacklogAgent(llm_client=llm, settings=settings, memory_path=Path("/tmp/test_bl.md"))

    story = {
        "title": "Set up local dev environment",
        "description": "Clone the repo and build it.",
        "acceptance_criteria": ["Repo cloned", "Build succeeds"],
        "labels": ["chore"],
    }

    # Mock GraphQL responses: first for project lookup, second for draft creation
    graphql_responses = [
        {"user": {"projectV2": {"id": "PVT_abc123"}}},
        {"addProjectV2DraftIssue": {"projectItem": {"id": "PVTI_item456"}}},
    ]

    with patch("sambot.agent.backlog.BacklogAgent.learn"):
        with patch("sambot.github.client.GitHubClient") as MockGH:
            mock_gh_instance = MagicMock()
            mock_gh_instance.graphql_sync.side_effect = graphql_responses
            MockGH.return_value = mock_gh_instance

            result = agent.create_backlog_item(story)

    assert result["title"] == "Set up local dev environment"
    assert "projects/1" in result["url"]
    assert result["item_id"] == "PVTI_item456"
    assert mock_gh_instance.graphql_sync.call_count == 2


# ---------- New tool tests ----------


def test_tool_executor_search_files(tmp_path: Path):
    """ToolExecutor.search_files finds files by glob pattern."""
    from sambot.agent.tools import ToolExecutor

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("print('hi')")
    (tmp_path / "src" / "utils.py").write_text("pass")
    (tmp_path / "README.md").write_text("# Readme")

    executor = ToolExecutor(tmp_path)
    result = executor.search_files("*.py")
    assert result.success is True
    assert "main.py" in result.output
    assert "utils.py" in result.output
    assert "README.md" not in result.output


def test_tool_executor_search_files_no_match(tmp_path: Path):
    """ToolExecutor.search_files returns message when nothing found."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    result = executor.search_files("*.rs")
    assert result.success is True
    assert "no files" in result.output.lower()


def test_tool_executor_grep_file(tmp_path: Path):
    """ToolExecutor.grep_file searches for patterns inside files."""
    from sambot.agent.tools import ToolExecutor

    (tmp_path / "code.py").write_text("def hello():\n    return 'world'\n")
    executor = ToolExecutor(tmp_path)
    result = executor.grep_file("hello", ".")
    assert result.success is True
    assert "hello" in result.output


def test_tool_executor_run_command(tmp_path: Path):
    """ToolExecutor.run_command runs shell commands."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    result = executor.run_command("echo 'sambot test'")
    assert result.success is True
    assert "sambot test" in result.output


def test_tool_executor_run_command_blocks_protected_branches(tmp_path: Path):
    """ToolExecutor.run_command blocks pushes to protected branches."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)

    result = executor.run_command("git checkout develop")
    assert result.success is False
    assert "protected" in result.output.lower() or "blocked" in result.output.lower()

    result = executor.run_command("git push origin main")
    assert result.success is False
    assert "protected" in result.output.lower() or "blocked" in result.output.lower()


def test_tool_executor_run_command_blocks_dangerous(tmp_path: Path):
    """ToolExecutor.run_command blocks dangerous commands."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    result = executor.run_command("rm -rf /")
    assert result.success is False
    assert "blocked" in result.output.lower() or "dangerous" in result.output.lower()


def test_tool_executor_run_command_timeout(tmp_path: Path):
    """ToolExecutor.run_command enforces timeout."""
    from sambot.agent.tools import ToolExecutor

    executor = ToolExecutor(tmp_path)
    # min timeout is 10s in run_command, so use sleep 15 vs timeout=10
    result = executor.run_command("sleep 15", timeout=10)
    assert result.success is False
    assert "timed out" in result.output.lower()


def test_tool_executor_execute_new_tools(tmp_path: Path):
    """ToolExecutor.execute dispatches new tools correctly."""
    from sambot.agent.tools import ToolExecutor

    (tmp_path / "test.py").write_text("pass")
    executor = ToolExecutor(tmp_path)

    result = executor.execute("search_files", {"pattern": "*.py"})
    assert result.success is True

    result = executor.execute("grep_file", {"pattern": "pass"})
    assert result.success is True

    result = executor.execute("run_command", {"command": "echo ok"})
    assert result.success is True


# ---------- AgentResult blocked tests ----------


def test_agent_result_blocked_summary():
    """AgentResult.summary reflects blocked state."""
    from sambot.agent.loop import AgentResult

    result = AgentResult(
        success=False,
        passes_used=3,
        blocked=True,
        error="Tests still failing after 3 passes",
    )
    assert "Blocked" in result.summary
