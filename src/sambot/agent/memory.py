"""Persistent memory management with LLM-powered compression."""

from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger()

# Default memory file path (relative to project root, not workspace)
DEFAULT_MEMORY_PATH = Path("MEMORY.md")


class MemoryManager:
    """Manages persistent project memory with compression."""

    def __init__(self, memory_path: Path | None = None) -> None:
        self._memory_path = memory_path or DEFAULT_MEMORY_PATH

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
            self._memory_path.write_text(content)
            logger.info("memory.saved", path=str(self._memory_path), size=len(content))
        except Exception as e:
            logger.error("memory.save_error", error=str(e))
            raise

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


def compress_memory(llm_client, current_memory: str, new_facts: str) -> str:
    """
    Use Claude to compress new facts into the existing memory.

    This merges new learnings from a completed job into the project memory,
    keeping it concise while preserving all important facts.

    Args:
        llm_client: The LLM client instance (to avoid circular imports)
        current_memory: The current MEMORY.md contents
        new_facts: New facts/learnings from the completed job

    Returns:
        Updated memory content
    """
    prompt = (
        "You are managing a project memory file. Your job is to merge new facts "
        "into the existing memory while keeping it concise and well-organized.\n\n"
        "Rules:\n"
        "- Preserve ALL important facts (architecture decisions, conventions, gotchas)\n"
        "- Remove redundant or outdated information\n"
        "- Keep the same markdown structure and sections\n"
        "- Be concise — compress, don't just append\n"
        "- Update dates and status fields\n"
        "- Keep the file under 500 lines\n\n"
        f"## Current Memory\n\n{current_memory}\n\n"
        f"## New Facts to Integrate\n\n{new_facts}\n\n"
        "Return the complete updated memory file content."
    )

    # Use raw completion without memory injection to avoid recursion
    response = llm_client.complete_raw(prompt, max_tokens=8192)
    logger.info("memory.compressed", old_size=len(current_memory), new_size=len(response))
    return response
