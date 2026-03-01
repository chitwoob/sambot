"""Agent tool definitions for file operations, testing, and Q&A."""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path
    pass

logger = structlog.get_logger()

# Commands that are NEVER allowed even inside Docker
_BLOCKED_COMMANDS = {"rm -rf /", "mkfs", "dd if=", ":(){", "fork bomb"}

# Max output size from run_command (characters)
_MAX_COMMAND_OUTPUT = 50_000


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
        "name": "search_files",
        "description": (
            "Search for files matching a glob pattern in the workspace. "
            "Useful for discovering project structure, finding config files, "
            "package manifests (package.json, Cargo.toml, go.mod, etc.), "
            "and understanding the tech stack. "
            "Returns matching file paths relative to workspace root."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern to match file names. Examples: "
                        "'*.py', '**/*.ts', 'package.json', 'Dockerfile*', "
                        "'**/Cargo.toml', '*.go'"
                    ),
                },
                "directory": {
                    "type": "string",
                    "description": "Directory to search in (relative). Defaults to workspace root.",
                    "default": ".",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_file",
        "description": (
            "Search for a text pattern (regex) inside files in the workspace. "
            "Returns matching lines with file paths and line numbers. "
            "Useful for finding usages, imports, function definitions, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in (relative). Defaults to '.'.",
                    "default": ".",
                },
                "include": {
                    "type": "string",
                    "description": "Optional glob to filter files, e.g. '*.py' or '*.ts'",
                    "default": "",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command in the workspace directory. "
            "Use this for: building projects, running linters, installing deps, "
            "git operations (on feature branches ONLY), and other dev tasks. "
            "NEVER run destructive commands. NEVER run on develop or main branches. "
            "Commands time out after 120 seconds."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default 120, max 300)",
                    "default": 120,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "run_tests",
        "description": (
            "Run the project's test suite. The runner will auto-detect the test "
            "framework from the project structure (pytest, npm test, cargo test, "
            "go test, etc.) or use Docker Compose if a docker-compose.yml is present. "
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
    {
        "name": "request_docker_permission",
        "description": (
            "Request permission to run a newly generated Docker or docker-compose file. "
            "You MUST call this before running any Docker file you created for the first time. "
            "If the file was already approved, this returns immediately. "
            "Otherwise, it asks the team in Slack and waits for approval."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the Docker file (e.g. 'Dockerfile', 'docker-compose.yml')",
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what this Docker file does and why it's needed",
                },
            },
            "required": ["file_path", "description"],
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

    def search_files(self, pattern: str, directory: str = ".") -> ToolResult:
        """Search for files matching a glob pattern."""
        try:
            search_dir = self._resolve_path(directory)
            if not search_dir.exists() or not search_dir.is_dir():
                return ToolResult(success=False, output=f"Directory not found: {directory}")

            matches: list[str] = []
            workspace_root = self._work_dir.resolve()

            for path in search_dir.rglob("*"):
                if path.is_file():
                    rel = str(path.relative_to(workspace_root))
                    # Skip hidden dirs and __pycache__
                    parts = rel.split("/")
                    if any(p.startswith(".") or p == "__pycache__" or p == "node_modules" for p in parts):
                        continue
                    if fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(rel, pattern):
                        matches.append(rel)

                if len(matches) >= 200:
                    break

            output = "\n".join(sorted(matches)) if matches else f"No files matching '{pattern}' found"
            logger.info("tool.search_files", pattern=pattern, directory=directory, count=len(matches))
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, output=f"Error searching files: {e}")

    def grep_file(self, pattern: str, path: str = ".", include: str = "") -> ToolResult:
        """Search for a regex pattern inside files."""
        try:
            target = self._resolve_path(path)
            cmd = ["grep", "-rn", "--color=never", "-E", pattern]
            if include:
                cmd.extend(["--include", include])
            cmd.append(str(target))

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self._work_dir,
            )

            output = result.stdout
            if len(output) > _MAX_COMMAND_OUTPUT:
                output = output[:_MAX_COMMAND_OUTPUT] + "\n... (output truncated)"

            if not output.strip():
                output = f"No matches found for pattern '{pattern}'"

            # Make paths relative to workspace
            workspace_str = str(self._work_dir.resolve()) + "/"
            output = output.replace(workspace_str, "")

            logger.info("tool.grep_file", pattern=pattern, path=path)
            return ToolResult(success=True, output=output)
        except subprocess.TimeoutExpired:
            return ToolResult(success=False, output="grep timed out after 30 seconds")
        except Exception as e:
            return ToolResult(success=False, output=f"Error grepping: {e}")

    def run_command(self, command: str, timeout: int = 120) -> ToolResult:
        """Run a shell command in the workspace.

        Safety:
        - Blocks obviously destructive commands
        - Prevents operations on develop/main branches
        - Enforces timeout (max 300s)
        """
        # Safety checks
        cmd_lower = command.lower().strip()
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return ToolResult(success=False, output=f"Blocked: dangerous command detected")

        # Prevent direct checkout/push to protected branches
        protected = ["main", "master", "develop"]
        for branch in protected:
            if f"git checkout {branch}" in cmd_lower or f"git switch {branch}" in cmd_lower:
                return ToolResult(
                    success=False,
                    output=f"Blocked: cannot checkout protected branch '{branch}'. Work on feature branches only.",
                )
            if f"git push origin {branch}" in cmd_lower or f"git push --force origin {branch}" in cmd_lower:
                return ToolResult(
                    success=False,
                    output=f"Blocked: cannot push to protected branch '{branch}'.",
                )

        timeout = min(max(timeout, 10), 300)

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self._work_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            output = result.stdout + result.stderr
            if len(output) > _MAX_COMMAND_OUTPUT:
                output = output[:_MAX_COMMAND_OUTPUT] + "\n... (output truncated)"

            if not output.strip():
                output = f"(command completed with exit code {result.returncode})"

            logger.info(
                "tool.run_command",
                command=command[:100],
                exit_code=result.returncode,
            )
            return ToolResult(
                success=result.returncode == 0,
                output=output,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False,
                output=f"Command timed out after {timeout} seconds",
            )
        except Exception as e:
            return ToolResult(success=False, output=f"Error running command: {e}")

    def execute(self, tool_name: str, tool_input: dict) -> ToolResult:
        """Execute a tool by name with the given input."""
        if tool_name == "read_file":
            return self.read_file(tool_input["path"])
        elif tool_name == "write_file":
            return self.write_file(tool_input["path"], tool_input["content"])
        elif tool_name == "list_directory":
            return self.list_directory(tool_input["path"])
        elif tool_name == "search_files":
            return self.search_files(
                tool_input["pattern"],
                tool_input.get("directory", "."),
            )
        elif tool_name == "grep_file":
            return self.grep_file(
                tool_input["pattern"],
                tool_input.get("path", "."),
                tool_input.get("include", ""),
            )
        elif tool_name == "run_command":
            return self.run_command(
                tool_input["command"],
                tool_input.get("timeout", 120),
            )
        else:
            return ToolResult(success=False, output=f"Unknown tool: {tool_name}")
