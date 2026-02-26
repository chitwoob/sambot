"""Anthropic Claude client wrapper — memory-aware.

Every LLM call can include the project memory as context via
``build_system_prompt`` from ``llm.prompts``.
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

    ``complete()`` auto-injects project memory into the system prompt
    using ``build_system_prompt``.  Use ``complete_raw()`` for calls
    that must NOT include memory (e.g., memory compression itself).
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
        """Update the memory content included in ``complete()`` calls."""
        self._memory = memory_content

    def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ) -> str:
        """Send a prompt to Claude with project memory injected.

        The memory is prepended to the system prompt automatically
        via ``build_system_prompt``.
        """
        from sambot.llm.prompts import build_system_prompt

        full_system = build_system_prompt(system, self._memory)

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
