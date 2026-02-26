"""Persistent memory management with LLM-powered compression.

Every agent has its own MemoryManager instance pointing at a memory file.
When new facts come in, ``compress_memory`` uses Claude to merge them into
the existing memory while respecting a configurable token budget so we
don't burn context window on stale information.
"""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger()

# Default memory file path (relative to project root, not workspace)
DEFAULT_MEMORY_PATH = Path("MEMORY.md")

# Approximate chars-per-token for budget estimation (conservative)
CHARS_PER_TOKEN = 4


class MemoryManager:
    """Manages persistent project memory with compression.

    Each agent (coder, backlog, etc.) can have its own MemoryManager
    with a dedicated memory file and token budget.
    """

    def __init__(
        self,
        memory_path: Path | None = None,
        max_tokens: int = 2000,
    ) -> None:
        self._memory_path = memory_path or DEFAULT_MEMORY_PATH
        self._max_tokens = max_tokens

    @property
    def max_tokens(self) -> int:
        """Soft token limit for this memory file."""
        return self._max_tokens

    @property
    def max_chars(self) -> int:
        """Approximate character limit derived from token budget."""
        return self._max_tokens * CHARS_PER_TOKEN

    def load(self) -> str:
        """Load the current project memory. Returns empty string if not found."""
        try:
            if self._memory_path.exists():
                content = self._memory_path.read_text()
                logger.info("memory.loaded", path=str(self._memory_path), size=len(content))
                return content
            logger.info("memory.not_found", path=str(self._memory_path))
            return ""
        except Exception as e:
            logger.error("memory.load_error", error=str(e))
            return ""

    def save(self, content: str) -> None:
        """Save updated memory content."""
        try:
            self._memory_path.parent.mkdir(parents=True, exist_ok=True)
            self._memory_path.write_text(content)
            logger.info("memory.saved", path=str(self._memory_path), size=len(content))
        except Exception as e:
            logger.error("memory.save_error", error=str(e))
            raise

    def is_over_budget(self) -> bool:
        """Return True if the current memory exceeds the soft token budget."""
        content = self.load()
        return len(content) > self.max_chars

    def build_story_context(
        self,
        story_title: str,
        story_body: str,
        labels: list[str] | None = None,
    ) -> str:
        """Build a context string for the agent combining memory + story."""
        memory = self.load()

        sections = []

        if memory:
            sections.append("## Project Memory\n")
            sections.append(memory)
            sections.append("")

        sections.append("## Current Story\n")
        sections.append(f"**Title:** {story_title}\n")
        sections.append(f"**Description:**\n{story_body}\n")

        if labels:
            sections.append(f"**Labels:** {', '.join(labels)}\n")

        return "\n".join(sections)


def compress_memory(
    llm_client,
    current_memory: str,
    new_facts: str,
    max_tokens: int = 2000,
) -> str:
    """Use Claude to compress new facts into the existing memory.

    This merges new learnings from a completed job into the project memory,
    keeping it concise while respecting the token budget.

    Args:
        llm_client: The LLM client instance (to avoid circular imports)
        current_memory: The current memory file contents
        new_facts: New facts/learnings to integrate
        max_tokens: Soft token budget — Claude is told to stay under this

    Returns:
        Updated memory content
    """
    from sambot.llm.prompts import MEMORY_COMPRESSION_SYSTEM

    max_chars = max_tokens * CHARS_PER_TOKEN

    system = MEMORY_COMPRESSION_SYSTEM.format(
        max_tokens=max_tokens,
        max_chars=max_chars,
    )

    prompt = (
        f"## Current Memory\n\n{current_memory}\n\n"
        f"## New Facts to Integrate\n\n{new_facts}\n\n"
        "Return the complete updated memory file content."
    )

    # Use raw completion without memory injection to avoid recursion
    response = llm_client.complete_raw(prompt, system=system, max_tokens=8192)
    logger.info(
        "memory.compressed",
        old_size=len(current_memory),
        new_size=len(response),
        budget_chars=max_chars,
    )
    return response
