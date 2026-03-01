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


def _get_backlog_memory_path() -> Path:
    """Return the backlog agent memory path from settings, or fallback."""
    try:
        from sambot.config import get_settings
        return get_settings().backlog_memory_path
    except Exception:
        return DEFAULT_BACKLOG_MEMORY_PATH


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
            memory_path=memory_path or _get_backlog_memory_path(),
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

    def classify_intent(self, message: str, conversation_context: str = "") -> str:
        """Classify whether the user wants to create the ticket or refine.

        Returns:
            ``'create'`` if the user is confirming the story is ready,
            ``'refine'`` if they are providing more information.
        """
        prompt = (
            "You are classifying the intent of a message in a backlog "
            "refinement thread.\n\n"
            f"Conversation so far:\n{conversation_context}\n\n"
            f"Latest message from the user:\n{message}\n\n"
            "Does the user want to:\n"
            "A) CREATE - Submit/create the ticket now "
            "(they are done refining, said something like 'create it', "
            "'let's do it', 'looks good', etc.)\n"
            "B) REFINE - Provide more information, answer questions, "
            "or request changes to the draft\n\n"
            "Respond with exactly one word: CREATE or REFINE"
        )
        response = self._llm.complete_raw(prompt, max_tokens=10, temperature=0.0)
        intent = "create" if "CREATE" in response.strip().upper() else "refine"
        logger.info("backlog.intent_classified", intent=intent, message=message[:60])
        return intent

    def create_backlog_item(self, story: dict) -> dict:
        """Create a draft issue on the GitHub Project board.

        Uses the ``addProjectV2DraftIssue`` GraphQL mutation so the
        item lives only on the project board — no repo Issue is created.

        Returns:
            dict with ``title``, ``url`` (project board link), and
            ``item_id`` (project item node ID).
        """
        from sambot.github.client import GitHubClient

        gh = GitHubClient(self._settings)

        # Build body
        body_parts: list[str] = []
        if story.get("description"):
            body_parts.append(story["description"])
        if story.get("acceptance_criteria"):
            body_parts.append("\n## Acceptance Criteria")
            for ac in story["acceptance_criteria"]:
                body_parts.append(f"- [ ] {ac}")
        if story.get("labels"):
            body_parts.append(f"\n**Labels:** {', '.join(story['labels'])}")
        body = "\n".join(body_parts)

        title = story.get("title", "Untitled")

        # Look up project node ID
        project_query = """
        query($login: String!, $projectNumber: Int!) {
          user(login: $login) {
            projectV2(number: $projectNumber) { id }
          }
        }
        """
        data = gh.graphql_sync(project_query, {
            "login": self._settings.resolved_project_owner,
            "projectNumber": self._settings.github_project_number,
        })
        project_id = data["user"]["projectV2"]["id"]

        # Create a draft issue on the project board
        mutation = """
        mutation($projectId: ID!, $title: String!, $body: String!) {
          addProjectV2DraftIssue(
            input: {projectId: $projectId, title: $title, body: $body}
          ) {
            projectItem { id }
          }
        }
        """
        result = gh.graphql_sync(mutation, {
            "projectId": project_id,
            "title": title,
            "body": body,
        })
        item_id = result["addProjectV2DraftIssue"]["projectItem"]["id"]
        kind = _item_kind(story)
        logger.info("backlog.created", kind=kind, title=title, item_id=item_id)

        # Build project board URL
        owner = self._settings.resolved_project_owner
        project_num = self._settings.github_project_number
        project_url = f"https://github.com/users/{owner}/projects/{project_num}"

        # Learn from the creation
        kind = _item_kind(story)
        self.learn(f"Created {kind.lower()}: {title}. Labels: {', '.join(story.get('labels', []))}.")

        gh.close()
        return {
            "title": title,
            "url": project_url,
            "item_id": item_id,
        }

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


_LABEL_TO_KIND = {
    "bug": "Bug",
    "feature": "Story",
    "story": "Story",
    "improvement": "Story",
    "chore": "Task",
    "task": "Task",
}


def _item_kind(story: dict) -> str:
    """Derive a human-friendly type (Story, Task, Bug) from labels."""
    for label in story.get("labels", []):
        kind = _LABEL_TO_KIND.get(label.lower())
        if kind:
            return kind
    return "Story"
