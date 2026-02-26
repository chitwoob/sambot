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
    """All 5 expected tools are defined."""
    from sambot.agent.tools import TOOL_DEFINITIONS

    names = {t["name"] for t in TOOL_DEFINITIONS}
    assert names == {"read_file", "write_file", "list_directory", "run_tests", "ask_question"}


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
