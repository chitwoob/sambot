"""Agent tool definitions for file operations, testing, and Q&A."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path
    pass

logger = structlog.get_logger()


# --- Tool schemas for Claude tool_use ---

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file at the given path (relative to the workspace root). "
            "Use this to understand existing code before making changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path from workspace root",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Write content to a file at the given path (relative to workspace root). "
            "Creates the file if it doesn't exist, overwrites if it does. "
            "Creates parent directories as needed. "
            "Always write the COMPLETE file content, not just a diff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative file path from workspace root",
                },
                "content": {
                    "type": "string",
                    "description": "Complete file content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and directories at a given path (relative to workspace root). "
            "Returns a list of names. Directories end with /."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory path from workspace root. Use '.' for root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "run_tests",
        "description": (
            "Run the project's test suite using pytest. "
            "Returns test output including pass/fail counts and error details. "
            "You MUST run tests after making code changes and before completing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "test_path": {
                    "type": "string",
                    "description": "Optional: specific test file or directory to run. Defaults to all tests.",
                    "default": "",
                },
            },
            "required": [],
        },
    },
    {
        "name": "ask_question",
        "description": (
            "Ask the development team a technical or business question via Slack. "
            "Use this when you need clarification about requirements, business logic, "
            "or technical decisions that you cannot determine from the code or story alone. "
            "The agent will pause until a human responds. "
            "Be specific and concise in your question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the team",
                },
                "context": {
                    "type": "string",
                    "description": "Brief context about why you're asking (helps the team answer)",
                },
            },
            "required": ["question"],
        },
    },
]


@dataclass
class ToolResult:
    """Result from executing a tool."""

    success: bool
    output: str


class ToolExecutor:
    """Executes agent tools against the workspace."""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    def _resolve_path(self, relative_path: str) -> Path:
        """Resolve a relative path against the workspace root safely."""
        resolved = (self._work_dir / relative_path).resolve()
        # Prevent path traversal outside workspace
        if not str(resolved).startswith(str(self._work_dir.resolve())):
            raise ValueError(f"Path traversal detected: {relative_path}")
        return resolved

    def read_file(self, path: str) -> ToolResult:
        """Read a file from the workspace."""
        try:
            full_path = self._resolve_path(path)
            if not full_path.exists():
                return ToolResult(success=False, output=f"File not found: {path}")
            if not full_path.is_file():
                return ToolResult(success=False, output=f"Not a file: {path}")

            content = full_path.read_text()
            logger.info("tool.read_file", path=path, size=len(content))
            return ToolResult(success=True, output=content)
        except Exception as e:
            return ToolResult(success=False, output=f"Error reading {path}: {e}")

    def write_file(self, path: str, content: str) -> ToolResult:
        """Write content to a file in the workspace."""
        try:
            full_path = self._resolve_path(path)
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
            logger.info("tool.write_file", path=path, size=len(content))
            return ToolResult(success=True, output=f"Successfully wrote {path} ({len(content)} bytes)")
        except Exception as e:
            return ToolResult(success=False, output=f"Error writing {path}: {e}")

    def list_directory(self, path: str) -> ToolResult:
        """List contents of a directory in the workspace."""
        try:
            full_path = self._resolve_path(path)
            if not full_path.exists():
                return ToolResult(success=False, output=f"Directory not found: {path}")
            if not full_path.is_dir():
                return ToolResult(success=False, output=f"Not a directory: {path}")

            entries = []
            for entry in sorted(full_path.iterdir()):
                # Skip hidden files and __pycache__
                if entry.name.startswith(".") or entry.name == "__pycache__":
                    continue
                name = entry.name + "/" if entry.is_dir() else entry.name
                entries.append(name)

            output = "\n".join(entries) if entries else "(empty directory)"
            logger.info("tool.list_directory", path=path, count=len(entries))
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, output=f"Error listing {path}: {e}")

    def execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Execute a tool by name with the given input."""
        if tool_name == "read_file":
            return self.read_file(tool_input["path"])
        elif tool_name == "write_file":
            return self.write_file(tool_input["path"], tool_input["content"])
        elif tool_name == "list_directory":
            return self.list_directory(tool_input["path"])
        else:
            return ToolResult(success=False, output=f"Unknown tool: {tool_name}")
