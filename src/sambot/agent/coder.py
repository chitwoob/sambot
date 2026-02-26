"""Code generation via Claude tool use."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from sambot.agent.tools import TOOL_DEFINITIONS, ToolExecutor, ToolResult

if TYPE_CHECKING:

    from sambot.agent.test_runner import TestRunner

logger = structlog.get_logger()


class Coder:
    """Generates and modifies code using Claude with tool use."""

    def __init__(
        self,
        anthropic_client,
        tool_executor: ToolExecutor,
        test_runner: TestRunner,
        model: str = "claude-sonnet-4-20250514",
    ) -> None:
        self._client = anthropic_client
        self._tools = tool_executor
        self._test_runner = test_runner
        self._model = model
        self._conversation: list[dict[str, Any]] = []

    def execute_pass(
        self,
        system_prompt: str,
        user_message: str,
        on_progress: Any | None = None,
        ask_question_handler: Any | None = None,
    ) -> dict:
        """
        Execute one coding pass: send message to Claude, handle tool calls.

        Args:
            system_prompt: System prompt including memory context
            user_message: The instruction/story for this pass
            on_progress: Optional callback for progress updates
            ask_question_handler: Callback for handling ask_question tool
                                  Signature: (question: str, context: str) -> str

        Returns:
            dict with keys: success, message, files_changed, test_result
        """
        self._conversation.append({"role": "user", "content": user_message})

        files_changed: list[str] = []
        test_result = None

        # Loop: get response → execute tools → feed results back
        max_tool_rounds = 30  # Safety limit
        for round_num in range(max_tool_rounds):
            logger.info("coder.round", round=round_num + 1)

            response = self._client.messages.create(
                model=self._model,
                max_tokens=16384,
                system=system_prompt,
                messages=self._conversation,
                tools=TOOL_DEFINITIONS,
            )

            logger.info(
                "coder.response",
                stop_reason=response.stop_reason,
                usage_in=response.usage.input_tokens,
                usage_out=response.usage.output_tokens,
            )

            # Collect text content and tool uses
            assistant_content = response.content
            self._conversation.append({"role": "assistant", "content": assistant_content})

            # If no tool use, we're done with this pass
            if response.stop_reason == "end_turn":
                final_text = ""
                for block in assistant_content:
                    if hasattr(block, "text"):
                        final_text += block.text
                return {
                    "success": True,
                    "message": final_text,
                    "files_changed": files_changed,
                    "test_result": test_result,
                }

            # Process tool calls
            tool_results = []
            for block in assistant_content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input
                tool_id = block.id

                if on_progress:
                    on_progress(f"🔧 Using tool: {tool_name}")

                logger.info("coder.tool_call", tool=tool_name, input_keys=list(tool_input.keys()))

                # Handle special tools
                if tool_name == "run_tests":
                    test_path = tool_input.get("test_path", "")
                    test_result = self._test_runner.run(test_path)
                    result = ToolResult(
                        success=test_result.success,
                        output=test_result.output,
                    )
                    if on_progress:
                        on_progress(f"🧪 {test_result.summary}")

                elif tool_name == "ask_question":
                    question = tool_input["question"]
                    context = tool_input.get("context", "")
                    if ask_question_handler:
                        answer = ask_question_handler(question, context)
                        result = ToolResult(success=True, output=f"Answer: {answer}")
                    else:
                        result = ToolResult(
                            success=True,
                            output="No Q&A channel available. Proceed with your best judgment.",
                        )
                    if on_progress:
                        on_progress(f"❓ Asked: {question[:80]}...")

                elif tool_name == "write_file":
                    result = self._tools.execute(tool_name, tool_input)
                    if result.success:
                        files_changed.append(tool_input["path"])
                    if on_progress:
                        on_progress(f"📝 Wrote: {tool_input['path']}")

                else:
                    result = self._tools.execute(tool_name, tool_input)
                    if on_progress and tool_name == "read_file":
                        on_progress(f"📖 Read: {tool_input['path']}")

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result.output,
                })

            self._conversation.append({"role": "user", "content": tool_results})

        # Exhausted tool rounds
        return {
            "success": False,
            "message": "Agent exhausted maximum tool call rounds",
            "files_changed": files_changed,
            "test_result": test_result,
        }

    def reset_conversation(self) -> None:
        """Clear conversation history for a fresh pass."""
        self._conversation = []

    @property
    def conversation(self) -> list[dict]:
        """Access the conversation history."""
        return self._conversation
