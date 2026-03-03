"""Test runner — executes the project's native test suite.

Language auto-detection order (checked in the resolved test directory):
  pubspec.yaml   → flutter test
  package.json   → npm test  (yarn test if yarn.lock exists)
  Cargo.toml     → cargo test
  go.mod         → go test ./...
  pom.xml        → mvn test -q
  build.gradle*  → ./gradlew test  (or gradle test)
  pyproject.toml / setup.py / pytest.ini  → python -m pytest  (default)

The ``test_path`` parameter doubles as a subdirectory hint for monorepos:
  - If it resolves to a directory that contains a manifest, tests run there.
  - If it resolves to a file, the parent directory is used for detection
    and the file path is appended to the command (pytest only).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

logger = structlog.get_logger()


@dataclass
class TestResult:
    """Result from running the test suite."""

    success: bool
    exit_code: int
    output: str
    language: str = ""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total: int = 0
    failure_details: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Human-readable summary."""
        status = "PASSED ✅" if self.success else "FAILED ❌"
        lang = f" [{self.language}]" if self.language else ""
        counts = (
            f"{self.passed} passed, {self.failed} failed, {self.errors} errors"
            if self.total > 0
            else "see output"
        )
        return f"Tests{lang} {status}: {counts}"


class TestRunner:
    """Runs the project's native test suite with automatic language detection."""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    def _detect(self, search_dir: Path, file_hint: str = "") -> tuple[list[str], str]:
        """Return (command, language) for the project rooted at *search_dir*.

        Args:
            search_dir: Directory to inspect for manifest files.
            file_hint:  Optional specific test file to target (pytest only).
        """
        # Flutter / Dart
        if (search_dir / "pubspec.yaml").exists():
            return ["flutter", "test"], "flutter"

        # Node.js — prefer yarn if lock-file present
        if (search_dir / "package.json").exists():
            try:
                pkg = json.loads((search_dir / "package.json").read_text(errors="replace"))
                has_test_script = "test" in pkg.get("scripts", {})
            except Exception:
                has_test_script = True  # assume it exists

            if has_test_script:
                runner = "yarn" if (search_dir / "yarn.lock").exists() else "npm"
                return [runner, "test"], "nodejs"

        # Rust
        if (search_dir / "Cargo.toml").exists():
            return ["cargo", "test"], "rust"

        # Go
        if (search_dir / "go.mod").exists():
            return ["go", "test", "./..."], "go"

        # Maven
        if (search_dir / "pom.xml").exists():
            return ["mvn", "test", "-q"], "java-maven"

        # Gradle
        for gradle_file in ("build.gradle", "build.gradle.kts"):
            if (search_dir / gradle_file).exists():
                gradlew = "./gradlew" if (search_dir / "gradlew").exists() else "gradle"
                return [gradlew, "test"], "java-gradle"

        # Python (default)
        cmd = ["python", "-m", "pytest", "-v", "--tb=short", "--no-header"]
        if file_hint:
            cmd.append(file_hint)
        return cmd, "python"

    def _resolve_test_dir(self, test_path: str) -> tuple[Path, str]:
        """Return (cwd_for_tests, file_hint_for_pytest).

        If test_path points to a file → use its parent as cwd, file as hint.
        If test_path points to a directory → use it as cwd, no file hint.
        Empty → use workspace root.
        Also scans one level of subdirectories to handle monorepos where the
        root has no manifest of its own.
        """
        if not test_path:
            # Try root first; if no manifest found scan apps/* / packages/* etc.
            if not self._has_manifest(self._work_dir):
                for sub in sorted(self._work_dir.iterdir()):
                    if sub.is_dir() and self._has_manifest(sub):
                        logger.info("test_runner.monorepo_fallback", subdir=str(sub))
                        return sub, ""
            return self._work_dir, ""

        candidate = self._work_dir / test_path
        if candidate.is_file():
            return candidate.parent, str(candidate)
        if candidate.is_dir():
            return candidate, ""
        # Path doesn't exist yet — fall back to root
        return self._work_dir, test_path

    @staticmethod
    def _has_manifest(directory: Path) -> bool:
        manifests = [
            "pubspec.yaml", "package.json", "Cargo.toml",
            "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
            "pyproject.toml", "setup.py", "pytest.ini",
        ]
        return any((directory / m).exists() for m in manifests)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, test_path: str = "") -> TestResult:
        """Run the project's test suite.

        Args:
            test_path: Optional subdirectory or test file (relative to workspace
                       root).  Pass a subdirectory to target one app in a
                       monorepo.  Empty = auto-detect from workspace root.

        Returns:
            TestResult.  success is True iff exit code is 0.
        """
        cwd, file_hint = self._resolve_test_dir(test_path)
        cmd, language = self._detect(cwd, file_hint)

        logger.info(
            "test_runner.starting",
            language=language,
            cwd=str(cwd),
            cmd=" ".join(cmd),
        )

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            output = result.stdout + result.stderr
            parsed = self._parse_output(output, result.returncode, language)

            logger.info(
                "test_runner.completed",
                language=language,
                success=parsed.success,
                exit_code=result.returncode,
            )
            return parsed

        except subprocess.TimeoutExpired:
            logger.error("test_runner.timeout", language=language)
            return TestResult(
                success=False,
                exit_code=-1,
                language=language,
                output=f"Tests timed out after 300 seconds (command: {' '.join(cmd)})",
            )
        except FileNotFoundError:
            tool = cmd[0]
            logger.error("test_runner.tool_not_found", tool=tool, language=language)
            return TestResult(
                success=False,
                exit_code=-1,
                language=language,
                output=(
                    f"'{tool}' not found. "
                    f"Install dependencies with `run_command` before calling `run_tests`."
                ),
            )
        except Exception as e:
            logger.error("test_runner.error", error=str(e))
            return TestResult(
                success=False,
                exit_code=-1,
                language=language,
                output=f"Error running tests: {e}",
            )

    def _parse_output(self, output: str, exit_code: int, language: str = "") -> TestResult:
        """Parse test output.

        Primary success signal is always exit_code == 0 (universal).
        Count parsing is attempted for pytest output as a bonus for
        human-readable summaries.
        """
        passed = 0
        failed = 0
        errors = 0
        failure_details: list[str] = []

        # Only attempt count parsing for Python/pytest output
        if language in ("python", ""):
            for line in output.splitlines():
                if " passed" in line:
                    try:
                        idx = line.index(" passed")
                        num_str = "".join(
                            c for c in line[:idx].split()[-1] if c.isdigit()
                        )
                        if num_str:
                            passed = int(num_str)
                    except (ValueError, IndexError):
                        pass
                if " failed" in line:
                    try:
                        idx = line.index(" failed")
                        num_str = "".join(
                            c for c in line[:idx].split()[-1] if c.isdigit()
                        )
                        if num_str:
                            failed = int(num_str)
                    except (ValueError, IndexError):
                        pass
                if " error" in line and "error" not in line[:5]:
                    try:
                        idx = line.index(" error")
                        num_str = "".join(
                            c for c in line[:idx].split()[-1] if c.isdigit()
                        )
                        if num_str:
                            errors = int(num_str)
                    except (ValueError, IndexError):
                        pass
                if line.strip().startswith("FAILED"):
                    failure_details.append(line.strip())

        total = passed + failed + errors

        return TestResult(
            success=exit_code == 0,
            exit_code=exit_code,
            output=output,
            language=language,
            passed=passed,
            failed=failed,
            errors=errors,
            total=total,
            failure_details=failure_details,
        )
