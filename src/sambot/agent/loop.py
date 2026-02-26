"""Multi-pass agent loop — orchestrates the full coding workflow."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

from sambot.agent.coder import Coder
from sambot.agent.memory import MemoryManager, compress_memory
from sambot.agent.test_runner import TestRunner
from sambot.agent.tools import ToolExecutor
from sambot.llm.prompts import CODING_AGENT_SYSTEM

if TYPE_CHECKING:
    from pathlib import Path
    pass

logger = structlog.get_logger()


@dataclass
class AgentResult:
    """Result from a complete agent run."""

    success: bool
    passes_used: int
    files_changed: list[str] = field(default_factory=list)
    test_output: str = ""
    final_message: str = ""
    questions_asked: list[dict] = field(default_factory=list)
    error: str = ""

    @property
    def summary(self) -> str:
        if self.success:
            return (
                f"✅ Completed in {self.passes_used} pass(es). "
                f"Changed {len(self.files_changed)} file(s). "
                f"Asked {len(self.questions_asked)} question(s)."
            )
        return f"❌ Failed after {self.passes_used} pass(es): {self.error}"


class AgentLoop:
    """
    Orchestrates the multi-pass coding agent.

    The loop:
    1. Analyze story + memory + codebase
    2. Code + write tests using tools
    3. Run tests
    4. If tests fail → analyze errors → next pass
    5. If blocked → ask question via Slack → continue
    6. If all tests pass → done
    """

    def __init__(
        self,
        work_dir: Path,
        anthropic_client,
        memory_path: Path | None = None,
        max_passes: int = 5,
        model: str = "claude-sonnet-4-20250514",
        on_progress: Any | None = None,
        ask_question_handler: Any | None = None,
    ) -> None:
        self._work_dir = work_dir
        self._anthropic_client = anthropic_client
        self._max_passes = max_passes
        self._model = model
        self._on_progress = on_progress
        self._ask_question_handler = ask_question_handler
        self._questions_asked: list[dict] = []

        # Initialize components
        self._memory = MemoryManager(memory_path)
        self._tools = ToolExecutor(work_dir)
        self._test_runner = TestRunner(work_dir)
        self._coder = Coder(
            anthropic_client=anthropic_client,
            tool_executor=self._tools,
            test_runner=self._test_runner,
            model=model,
        )

    def _progress(self, message: str) -> None:
        """Send a progress update."""
        logger.info("agent.progress", message=message)
        if self._on_progress:
            self._on_progress(message)

    def _handle_question(self, question: str, context: str) -> str:
        """Handle an agent question — route to Slack or return default."""
        self._questions_asked.append({"question": question, "context": context})
        if self._ask_question_handler:
            return self._ask_question_handler(question, context)
        return "No Q&A channel available. Use your best judgment."

    def run(
        self,
        story_title: str,
        story_body: str,
        labels: list[str] | None = None,
    ) -> AgentResult:
        """
        Run the agent loop for a story.

        Args:
            story_title: Issue title
            story_body: Issue body/description
            labels: Issue labels (used for context)

        Returns:
            AgentResult with success status and details
        """
        self._progress(f"🚀 Starting agent for: {story_title}")
        self._questions_asked = []
        all_files_changed: list[str] = []

        # Build context from memory + story
        story_context = self._memory.build_story_context(story_title, story_body, labels)
        system_prompt = CODING_AGENT_SYSTEM + "\n\n" + story_context

        for pass_num in range(1, self._max_passes + 1):
            self._progress(f"📋 Pass {pass_num}/{self._max_passes}")

            # Build the user message for this pass
            if pass_num == 1:
                user_message = (
                    f"Implement the following story. Read the codebase first to understand "
                    f"the project structure, then make the necessary code changes AND write "
                    f"tests. Run tests when done.\n\n"
                    f"**Story:** {story_title}\n\n"
                    f"**Details:**\n{story_body}"
                )
            else:
                # On subsequent passes, we continue the conversation
                # The coder already has the context from previous passes
                user_message = (
                    "The tests failed in the previous pass. Analyze the failures above, "
                    "fix the code, and run tests again. Make sure all tests pass."
                )

            # Execute the coding pass
            pass_result = self._coder.execute_pass(
                system_prompt=system_prompt,
                user_message=user_message,
                on_progress=self._on_progress,
                ask_question_handler=self._handle_question,
            )

            # Track files changed
            for f in pass_result.get("files_changed", []):
                if f not in all_files_changed:
                    all_files_changed.append(f)

            test_result = pass_result.get("test_result")

            # Check if tests passed
            if test_result and test_result.success:
                self._progress(f"✅ All tests passed on pass {pass_num}!")

                return AgentResult(
                    success=True,
                    passes_used=pass_num,
                    files_changed=all_files_changed,
                    test_output=test_result.output,
                    final_message=pass_result.get("message", ""),
                    questions_asked=self._questions_asked,
                )

            # Tests didn't pass or weren't run — continue to next pass
            if test_result:
                self._progress(f"❌ Tests failed on pass {pass_num}: {test_result.summary}")
            else:
                self._progress(f"⚠️ No tests were run on pass {pass_num}")

        # Exhausted all passes
        last_test_output = ""
        if test_result:
            last_test_output = test_result.output

        return AgentResult(
            success=False,
            passes_used=self._max_passes,
            files_changed=all_files_changed,
            test_output=last_test_output,
            questions_asked=self._questions_asked,
            error=f"Tests still failing after {self._max_passes} passes",
        )

    def compress_and_save_memory(self, llm_client, new_facts: str) -> None:
        """Compress new facts into project memory after a successful run."""
        current_memory = self._memory.load()
        if not new_facts.strip():
            return

        self._progress("🧠 Compressing new facts into memory...")
        updated_memory = compress_memory(llm_client, current_memory, new_facts)
        self._memory.save(updated_memory)
        self._progress("🧠 Memory updated.")
