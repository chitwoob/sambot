"""Test runner — executes pytest and parses results."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()


@dataclass
class TestResult:
    """Result from running the test suite."""

    success: bool
    exit_code: int
    output: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    total: int = 0
    failure_details: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Human-readable summary."""
        status = "PASSED ✅" if self.success else "FAILED ❌"
        return (
            f"Tests {status}: {self.passed} passed, {self.failed} failed, "
            f"{self.errors} errors (total: {self.total})"
        )


class TestRunner:
    """Runs pytest in a workspace and parses results."""

    def __init__(self, work_dir: Path) -> None:
        self._work_dir = work_dir

    def run(self, test_path: str = "") -> TestResult:
        """
        Run pytest in the workspace.

        Args:
            test_path: Optional specific test file/dir. Empty = all tests.

        Returns:
            TestResult with parsed pass/fail counts and output.
        """
        cmd = ["python", "-m", "pytest", "-v", "--tb=short", "--no-header"]
        if test_path:
            cmd.append(test_path)

        logger.info("test_runner.starting", work_dir=str(self._work_dir), test_path=test_path or "all")

        try:
            result = subprocess.run(
                cmd,
                cwd=self._work_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )

            output = result.stdout + result.stderr
            parsed = self._parse_output(output, result.returncode)

            logger.info(
                "test_runner.completed",
                success=parsed.success,
                passed=parsed.passed,
                failed=parsed.failed,
            )
            return parsed

        except subprocess.TimeoutExpired:
            logger.error("test_runner.timeout")
            return TestResult(
                success=False,
                exit_code=-1,
                output="Tests timed out after 120 seconds",
            )
        except FileNotFoundError:
            logger.error("test_runner.pytest_not_found")
            return TestResult(
                success=False,
                exit_code=-1,
                output="pytest not found. Is it installed?",
            )
        except Exception as e:
            logger.error("test_runner.error", error=str(e))
            return TestResult(
                success=False,
                exit_code=-1,
                output=f"Error running tests: {e}",
            )

    def _parse_output(self, output: str, exit_code: int) -> TestResult:
        """Parse pytest output to extract pass/fail counts."""
        passed = 0
        failed = 0
        errors = 0
        failure_details: list[str] = []

        for line in output.splitlines():
            # Look for the summary line like "5 passed, 2 failed"
            if "passed" in line or "failed" in line or "error" in line:
                if " passed" in line:
                    try:
                        idx = line.index(" passed")
                        num_str = ""
                        i = idx - 1
                        while i >= 0 and (line[i].isdigit() or line[i] == " "):
                            if line[i].isdigit():
                                num_str = line[i] + num_str
                            i -= 1
                        if num_str:
                            passed = int(num_str)
                    except (ValueError, IndexError):
                        pass
                if " failed" in line:
                    try:
                        idx = line.index(" failed")
                        num_str = ""
                        i = idx - 1
                        while i >= 0 and (line[i].isdigit() or line[i] == " "):
                            if line[i].isdigit():
                                num_str = line[i] + num_str
                            i -= 1
                        if num_str:
                            failed = int(num_str)
                    except (ValueError, IndexError):
                        pass
                if " error" in line:
                    try:
                        idx = line.index(" error")
                        num_str = ""
                        i = idx - 1
                        while i >= 0 and (line[i].isdigit() or line[i] == " "):
                            if line[i].isdigit():
                                num_str = line[i] + num_str
                            i -= 1
                        if num_str:
                            errors = int(num_str)
                    except (ValueError, IndexError):
                        pass

            # Capture FAILED lines
            if line.strip().startswith("FAILED"):
                failure_details.append(line.strip())

        total = passed + failed + errors

        return TestResult(
            success=exit_code == 0,
            exit_code=exit_code,
            output=output,
            passed=passed,
            failed=failed,
            errors=errors,
            total=total,
            failure_details=failure_details,
        )
