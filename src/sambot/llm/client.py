"""Anthropic Claude client wrapper — memory-aware.

Every LLM call includes the project memory as context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anthropic
import structlog

if TYPE_CHECKING:
    from sambot.config import Settings

logger = structlog.get_logger()


class LLMClient:
    """Wrapper around the Anthropic SDK for Claude interactions.

    All calls to `complete()` automatically include the project memory
    in the system prompt. Use `complete_raw()` for calls without memory
    injection (e.g., memory compression itself).
    """

    def __init__(self, settings: Settings, memory_content: str = "") -> None:
        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = "claude-sonnet-4-20250514"
        self._memory = memory_content

    @property
    def raw_client(self) -> anthropic.Anthropic:
        """Access the underlying Anthropic client (for agent/coder use)."""
        return self._client

    @property
    def model(self) -> str:
        """Current model name."""
        return self._model

    def set_memory(self, memory_content: str) -> None:
        """Update the memory content included in all calls."""
        self._memory = memory_content

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Send a prompt to Claude with project memory injected.

        The memory is prepended to the system prompt automatically.
        """
        # Build system prompt with memory
        system_parts = []
        if self._memory:
            system_parts.append(f"## Project Memory\n\n{self._memory}")
        if system:
            system_parts.append(system)
        full_system = "\n\n---\n\n".join(system_parts) if system_parts else ""

        return self.complete_raw(
            prompt=prompt,
            system=full_system,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def complete_raw(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Send a prompt to Claude WITHOUT memory injection.

        Use this for operations that should not include memory
        (e.g., compressing memory itself to avoid recursion).
        """
        messages = [{"role": "user", "content": prompt}]

        kwargs: dict = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system

        response = self._client.messages.create(**kwargs)

        text = response.content[0].text
        logger.info(
            "llm.completed",
            model=self._model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            has_memory=bool(self._memory) and "complete_raw" not in str(kwargs.get("system", "")[:50]),
        )
        return text

    def refine_story(self, title: str, body: str) -> str:
        """Use Claude to refine a vague story into clear requirements."""
        from sambot.llm.prompts import STORY_REFINEMENT_SYSTEM

        return self.complete(
            prompt=f"Story Title: {title}\n\nStory Body:\n{body}",
            system=STORY_REFINEMENT_SYSTEM,
        )

    def generate_pr_description(self, story_title: str, diff_summary: str, story_body: str = "") -> str:
        """Generate a well-formed PR description from a story and diff."""
        from sambot.llm.prompts import PR_DESCRIPTION_SYSTEM

        return self.complete(
            prompt=(
                f"Story: {story_title}\n\n"
                f"Story Description:\n{story_body}\n\n"
                f"Changes Made:\n{diff_summary}"
            ),
            system=PR_DESCRIPTION_SYSTEM,
        )
