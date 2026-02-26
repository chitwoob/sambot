"""Backlog agent — builds and refines stories from Slack conversations.

Monitors the backlog Slack channel for feature requests, bug reports, and
improvement ideas.  Uses Claude to ask clarifying questions and, once a
story is ready, creates a well-structured GitHub issue.

The agent has its own memory file (``backlog_memory.md``) so it
accumulates project context independently from the coding agent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from sambot.agent.memory import MemoryManager, compress_memory
from sambot.llm.prompts import BACKLOG_AGENT_SYSTEM, build_system_prompt

if TYPE_CHECKING:
    from sambot.config import Settings
    from sambot.llm.client import LLMClient

logger = structlog.get_logger()

# Default memory file for the backlog agent
DEFAULT_BACKLOG_MEMORY_PATH = Path("backlog_memory.md")


class BacklogAgent:
    """Builds well-structured stories from Slack conversations.

    Flow:
    1. Receives a message (feature idea, bug report, etc.)
    2. Loads its own memory for project context
    3. Asks clarifying questions via Slack if needed
    4. Produces a structured story (title, description, acceptance
       criteria, labels)
    5. Creates a GitHub issue

    After each interaction, new facts are compressed into the backlog
    agent's memory file.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        settings: Settings,
        memory_path: Path | None = None,
    ) -> None:
        self._llm = llm_client
        self._settings = settings
        self._memory = MemoryManager(
            memory_path=memory_path or DEFAULT_BACKLOG_MEMORY_PATH,
            max_tokens=settings.sambot_memory_max_tokens,
        )

    # -- public API -----------------------------------------------------------

    def refine_idea(self, idea: str, conversation_context: str = "") -> dict:
        """Turn a raw idea into a structured story.

        Args:
            idea: The raw feature/bug description from Slack.
            conversation_context: Prior messages in the thread (optional).

        Returns:
            dict with keys: title, description, acceptance_criteria, labels,
            follow_up_questions
        """
        memory = self._memory.load()
        system = build_system_prompt(BACKLOG_AGENT_SYSTEM, memory)

        prompt = self._build_refinement_prompt(idea, conversation_context)
        response = self._llm.complete_raw(prompt, system=system, max_tokens=4096)

        parsed = self._parse_story_response(response)
        logger.info(
            "backlog.refined",
            title=parsed.get("title", "")[:60],
            labels=parsed.get("labels", []),
        )
        return parsed

    def answer_followup(self, original_idea: str, answer: str, current_draft: str) -> dict:
        """Incorporate a follow-up answer and re-refine the story.

        Args:
            original_idea: The original raw idea.
            answer: The human's answer to a clarifying question.
            current_draft: The current story draft (JSON-ish text).

        Returns:
            Updated story dict.
        """
        memory = self._memory.load()
        system = build_system_prompt(BACKLOG_AGENT_SYSTEM, memory)

        prompt = (
            f"Original idea:\n{original_idea}\n\n"
            f"Current story draft:\n{current_draft}\n\n"
            f"The team answered a follow-up question:\n{answer}\n\n"
            "Update the story with this new information. Return the full "
            "updated story in the same format."
        )
        response = self._llm.complete_raw(prompt, system=system, max_tokens=4096)
        return self._parse_story_response(response)

    def learn(self, new_facts: str) -> None:
        """Compress new facts into the backlog agent's memory.

        Call this after a story is created or after meaningful project
        context surfaces in conversation.
        """
        current = self._memory.load()
        if not new_facts.strip():
            return

        updated = compress_memory(
            self._llm,
            current,
            new_facts,
            max_tokens=self._memory.max_tokens,
        )
        self._memory.save(updated)
        logger.info("backlog.memory_updated")

    # -- internals ------------------------------------------------------------

    def _build_refinement_prompt(self, idea: str, conversation_context: str) -> str:
        """Build the prompt for story refinement."""
        parts = []
        if conversation_context:
            parts.append(f"## Slack Conversation\n\n{conversation_context}\n")
        parts.append(f"## Idea / Request\n\n{idea}\n")
        parts.append(
            "\n## Instructions\n\n"
            "Produce a well-structured story. Respond with EXACTLY this format:\n\n"
            "TITLE: <concise title>\n\n"
            "DESCRIPTION:\n<description with context and motivation>\n\n"
            "ACCEPTANCE CRITERIA:\n- <criterion 1>\n- <criterion 2>\n...\n\n"
            "LABELS: <comma-separated labels>\n\n"
            "FOLLOW-UP QUESTIONS:\n- <question 1 (if any)>\n"
            "(If no questions are needed, write FOLLOW-UP QUESTIONS: none)"
        )
        return "\n".join(parts)

    @staticmethod
    def _parse_story_response(response: str) -> dict:
        """Parse structured story text into a dict."""
        result: dict = {
            "title": "",
            "description": "",
            "acceptance_criteria": [],
            "labels": [],
            "follow_up_questions": [],
            "raw": response,
        }

        current_section = None
        buffer: list[str] = []

        for line in response.splitlines():
            stripped = line.strip()

            if stripped.upper().startswith("TITLE:"):
                _flush(result, current_section, buffer)
                result["title"] = stripped[len("TITLE:"):].strip()
                current_section = None
                buffer = []

            elif stripped.upper().startswith("DESCRIPTION:"):
                _flush(result, current_section, buffer)
                rest = stripped[len("DESCRIPTION:"):].strip()
                current_section = "description"
                buffer = [rest] if rest else []

            elif stripped.upper().startswith("ACCEPTANCE CRITERIA:"):
                _flush(result, current_section, buffer)
                current_section = "acceptance_criteria"
                buffer = []

            elif stripped.upper().startswith("LABELS:"):
                _flush(result, current_section, buffer)
                label_text = stripped[len("LABELS:"):].strip()
                result["labels"] = [
                    lb.strip() for lb in label_text.split(",") if lb.strip()
                ]
                current_section = None
                buffer = []

            elif stripped.upper().startswith("FOLLOW-UP QUESTIONS:"):
                _flush(result, current_section, buffer)
                rest = stripped[len("FOLLOW-UP QUESTIONS:"):].strip()
                if rest.lower() == "none":
                    current_section = None
                    buffer = []
                else:
                    current_section = "follow_up_questions"
                    buffer = []

            else:
                buffer.append(line)

        _flush(result, current_section, buffer)
        return result


def _flush(result: dict, section: str | None, buffer: list[str]) -> None:
    """Flush accumulated buffer lines into the appropriate result field."""
    if not section or not buffer:
        return

    if section == "description":
        result["description"] = "\n".join(buffer).strip()

    elif section in ("acceptance_criteria", "follow_up_questions"):
        for line in buffer:
            text = line.strip().lstrip("- ").strip()
            if text:
                result[section].append(text)
