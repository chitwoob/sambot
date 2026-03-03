"""Multi-pass agent loop — orchestrates the full coding workflow.

The AgentLoop now handles:
- Repo scanning to detect the tech stack (language-agnostic)
- Docker/docker-compose generation and permission management
- Branch safety (never touches develop or main directly)
- Test execution via the detected framework or Docker
- PR creation targeting develop
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog

from sambot.agent.coder import Coder
from sambot.agent.memory import MemoryManager, compress_memory
from sambot.agent.test_runner import TestRunner
from sambot.agent.tools import ToolExecutor
from sambot.llm.prompts import CODING_AGENT_SYSTEM, INFRA_AGENT_SYSTEM, build_system_prompt

# Labels that indicate the task is infrastructure / config work with no
# meaningful Python test suite.  For these tasks the agent succeeds as soon
# as Claude finishes cleanly (end_turn) rather than requiring pytest to pass.
_INFRA_LABELS: frozenset[str] = frozenset(
    {"infrastructure", "docker", "devops", "ci", "cd", "chore", "documentation", "docs"}
)

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
    blocked: bool = False
    error: str = ""

    @property
    def summary(self) -> str:
        if self.success:
            return (
                f"✅ Completed in {self.passes_used} pass(es). "
                f"Changed {len(self.files_changed)} file(s). "
                f"Asked {len(self.questions_asked)} question(s)."
            )
        if self.blocked:
            return f"🚫 Blocked after {self.passes_used} pass(es): {self.error}"
        return f"❌ Failed after {self.passes_used} pass(es): {self.error}"


class AgentLoop:
    """
    Orchestrates the multi-pass coding agent.

    The loop:
    1. Scan repo to detect tech stack
    2. Generate Docker/compose if missing, request permission
    3. Analyze story + memory + codebase
    4. Code + write tests using tools
    5. Run tests
    6. If tests fail → analyze errors → next pass
    7. If blocked → ask question via Slack → continue
    8. If all tests pass → done
    """

    def __init__(
        self,
        work_dir: Path,
        anthropic_client,
        memory_path: Path | None = None,
        max_passes: int = 5,
        max_memory_tokens: int = 2000,
        model: str = "claude-sonnet-4-5",
        on_progress: Any | None = None,
        ask_question_handler: Any | None = None,
        docker_permission_handler: Any | None = None,
    ) -> None:
        self._work_dir = work_dir
        self._anthropic_client = anthropic_client
        self._max_passes = max_passes
        self._model = model
        self._on_progress = on_progress
        self._ask_question_handler = ask_question_handler
        self._docker_permission_handler = docker_permission_handler
        self._questions_asked: list[dict] = []

        # Initialize components
        self._memory = MemoryManager(memory_path, max_tokens=max_memory_tokens)
        self._tools = ToolExecutor(work_dir)
        self._test_runner = TestRunner(work_dir)
        self._coder = Coder(
            anthropic_client=anthropic_client,
            tool_executor=self._tools,
            test_runner=self._test_runner,
            model=model,
        )
        self._coder.set_handlers(
            ask_question_handler=ask_question_handler,
            docker_permission_handler=docker_permission_handler,
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

    @staticmethod
    def _is_infra_task(labels: list[str] | None) -> bool:
        """Return True when labels signal an infrastructure / config story."""
        if not labels:
            return False
        return bool(_INFRA_LABELS.intersection({lbl.lower() for lbl in labels}))

    def run(
        self,
        story_title: str,
        story_body: str,
        labels: list[str] | None = None,
    ) -> AgentResult:
        """
        Run the agent loop for a story.

        The coder will:
        1. Scan the repo to understand the tech stack
        2. Generate Docker files if needed (with permission)
        3. Implement the story
        4. Run tests and iterate until they pass

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

        infra_task = self._is_infra_task(labels)
        # Infrastructure tasks only need one pass — they don't have a Python
        # test suite to run, and extra passes just burn tokens.
        effective_max_passes = 1 if infra_task else self._max_passes
        agent_system = INFRA_AGENT_SYSTEM if infra_task else CODING_AGENT_SYSTEM

        if infra_task:
            self._progress("🏗️ Infrastructure task detected — test loop disabled")

        # Build context from memory + story
        story_context = self._memory.build_story_context(story_title, story_body, labels)
        memory_content = self._memory.load()
        system_prompt = build_system_prompt(agent_system, memory_content) + "\n\n" + story_context

        test_result = None
        for pass_num in range(1, effective_max_passes + 1):
            self._progress(f"📋 Pass {pass_num}/{effective_max_passes}")

            # Build the user message for this pass
            if infra_task:
                # One-shot: explore → create files → commit. No test loop.
                user_message = (
                    f"Implement the following infrastructure story.\n\n"
                    f"**Step 1 — Discover**: Use list_directory and search_files to "
                    f"map the full repo structure, find all package manifests, existing "
                    f"Dockerfiles, build scripts, and README files.\n\n"
                    f"**Step 2 — Implement**: Create all required config/infrastructure "
                    f"files (Dockerfiles, docker-compose, scripts, docs, etc.). "
                    f"Do NOT write Python test files — this is not a Python project task. "
                    f"Validation is done by the team running the resulting Docker setup, "
                    f"not by pytest.\n\n"
                    f"**Step 3 — Verify syntax only**: You may run "
                    f"`docker compose config` or `yamllint` to check file syntax if those "
                    f"tools are available, but do NOT attempt to build or run containers.\n\n"
                    f"Remember: you are on a feature branch. Never push to develop or main.\n\n"
                    f"**Story:** {story_title}\n\n"
                    f"**Details:**\n{story_body}"
                )
            elif pass_num == 1:
                user_message = (
                    f"Implement the following story.\n\n"
                    f"**FIRST**: Scan the repo structure (list_directory, search_files) to "
                    f"understand the project layout, tech stack, and conventions. Look for "
                    f"package manifests, config files, and existing Docker files.\n\n"
                    f"**IF** the project doesn't have Docker/docker-compose files for "
                    f"building and testing, generate appropriate ones based on the detected "
                    f"stack. Call request_docker_permission before running any Docker files "
                    f"you create.\n\n"
                    f"**THEN**: Read relevant code, implement the changes, write tests, "
                    f"and run the test suite. All tests must pass.\n\n"
                    f"Remember: you are on a feature branch. Never push to develop or main.\n\n"
                    f"**Story:** {story_title}\n\n"
                    f"**Details:**\n{story_body}"
                )
            else:
                # Fresh conversation each pass — include failure context explicitly
                failure_context = ""
                if test_result and test_result.output:
                    failure_context = f"\n\n**Test output from previous pass:**\n```\n{test_result.output[:4000]}\n```"
                user_message = (
                    f"The tests failed in the previous pass. Analyze the failures below, "
                    f"fix the code, and run tests again. Make sure all tests pass. "
                    f"Remember: never push to develop or main."
                    f"{failure_context}"
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

            # --- Infrastructure tasks: succeed as soon as Claude finishes cleanly ---
            if infra_task:
                if pass_result.get("success"):
                    self._progress(f"✅ Infrastructure task completed on pass {pass_num}!")
                    return AgentResult(
                        success=True,
                        passes_used=pass_num,
                        files_changed=all_files_changed,
                        test_output="",
                        final_message=pass_result.get("message", ""),
                        questions_asked=self._questions_asked,
                    )
                # Claude didn't finish cleanly (tool-round exhaustion)
                return AgentResult(
                    success=False,
                    passes_used=pass_num,
                    files_changed=all_files_changed,
                    questions_asked=self._questions_asked,
                    blocked=True,
                    error="Agent exhausted tool rounds without completing the task",
                )

            # --- Code tasks: require tests to pass ---
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
            passes_used=effective_max_passes,
            files_changed=all_files_changed,
            test_output=last_test_output,
            questions_asked=self._questions_asked,
            blocked=True,
            error=f"Tests still failing after {self._max_passes} passes",
        )

    def compress_and_save_memory(self, llm_client, new_facts: str) -> None:
        """Compress new facts into project memory after a successful run."""
        current_memory = self._memory.load()
        if not new_facts.strip():
            return

        self._progress("🧠 Compressing new facts into memory...")
        updated_memory = compress_memory(
            llm_client,
            current_memory,
            new_facts,
            max_tokens=self._memory.max_tokens,
        )
        self._memory.save(updated_memory)
        self._progress("🧠 Memory updated.")
