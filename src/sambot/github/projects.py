"""GitHub Projects V2 operations via GraphQL."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from sambot.github.client import GitHubClient

logger = structlog.get_logger()

# GraphQL query to fetch project items (user-level project)
QUERY_PROJECT_ITEMS = """
query($login: String!, $projectNumber: Int!, $first: Int!) {
  user(login: $login) {
    projectV2(number: $projectNumber) {
      id
      title
      items(first: $first) {
        nodes {
          id
          fieldValues(first: 10) {
            nodes {
              ... on ProjectV2ItemFieldTextValue {
                text
                field { ... on ProjectV2FieldCommon { name } }
              }
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2FieldCommon { name } }
              }
              ... on ProjectV2ItemFieldNumberValue {
                number
                field { ... on ProjectV2FieldCommon { name } }
              }
            }
          }
          content {
            __typename
            ... on Issue {
              number
              title
              body
              state
              labels(first: 10) {
                nodes { name }
              }
            }
            ... on DraftIssue {
              title
              body
            }
            ... on PullRequest {
              number
              title
            }
          }
        }
      }
    }
  }
}
"""

# GraphQL mutation to update a project item's status field
MUTATION_UPDATE_STATUS = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(
    input: {
      projectId: $projectId
      itemId: $itemId
      fieldId: $fieldId
      value: { singleSelectOptionId: $optionId }
    }
  ) {
    projectV2Item { id }
  }
}
"""

# Query to get project field metadata (for looking up Status field ID and option IDs)
QUERY_PROJECT_FIELDS = """
query($login: String!, $projectNumber: Int!) {
  user(login: $login) {
    projectV2(number: $projectNumber) {
      id
      fields(first: 20) {
        nodes {
          ... on ProjectV2SingleSelectField {
            id
            name
            options { id name }
          }
          ... on ProjectV2FieldCommon {
            id
            name
          }
        }
      }
    }
  }
}
"""

# Mutation to convert a DraftIssue into a real Issue
MUTATION_CONVERT_DRAFT = """
mutation($itemId: ID!, $repositoryId: ID!) {
  convertProjectV2DraftIssueItemToIssue(
    input: {
      itemId: $itemId
      repositoryId: $repositoryId
    }
  ) {
    item {
      id
      content {
        ... on Issue {
          number
          title
          body
          state
          labels(first: 10) {
            nodes { name }
          }
        }
      }
    }
  }
}
"""

# Query to get the repository node ID (needed for draft conversion)
QUERY_REPO_ID = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    id
  }
}
"""


@dataclass
class ProjectItem:
    """A story/issue from the GitHub Project board."""

    item_id: str
    issue_number: int
    title: str
    body: str
    status: str
    labels: list[str]


class ProjectsClient:
    """Handles GitHub Projects V2 operations."""

    def __init__(self, github: GitHubClient, owner: str, repo: str, project_number: int) -> None:
        self._github = github
        self._owner = owner
        self._repo = repo
        self._project_number = project_number
        self._project_id: str | None = None
        self._repo_id: str | None = None
        self._status_field_id: str | None = None
        self._status_options: dict[str, str] = {}

    async def get_items(self, first: int = 50) -> list[ProjectItem]:
        """Fetch items from the project board.

        DraftIssues are automatically converted to real Issues so they
        get issue numbers and can be processed by the pipeline.
        """
        data = await self._github.graphql(
            QUERY_PROJECT_ITEMS,
            {
                "login": self._owner,
                "projectNumber": self._project_number,
                "first": first,
            },
        )

        project = data["user"]["projectV2"]
        self._project_id = project["id"]
        items = []
        drafts_to_convert: list[tuple[str, str, str]] = []  # (item_id, title, status)

        for node in project["items"]["nodes"]:
            content = node.get("content")
            if not content:
                continue

            content_type = content.get("__typename", "")

            # Collect DraftIssues for conversion
            if content_type == "DraftIssue":
                status = self._extract_status(node)
                drafts_to_convert.append((node["id"], content.get("title", "?"), status))
                continue

            if "number" not in content:
                continue

            status = self._extract_status(node)
            items.append(
                ProjectItem(
                    item_id=node["id"],
                    issue_number=content["number"],
                    title=content["title"],
                    body=content.get("body", ""),
                    status=status,
                    labels=[label["name"] for label in content.get("labels", {}).get("nodes", [])],
                )
            )

        # Auto-convert DraftIssues that are in a workable status (not Done)
        for item_id, title, status in drafts_to_convert:
            if status.lower() == "done":
                continue  # skip completed drafts — no need to convert
            try:
                converted = await self.convert_draft_to_issue(item_id)
                if converted:
                    converted_status = status  # status is preserved on the board
                    items.append(
                        ProjectItem(
                            item_id=converted["item"]["id"],
                            issue_number=converted["item"]["content"]["number"],
                            title=converted["item"]["content"]["title"],
                            body=converted["item"]["content"].get("body", ""),
                            status=converted_status,
                            labels=[
                                label["name"]
                                for label in converted["item"]["content"]
                                .get("labels", {})
                                .get("nodes", [])
                            ],
                        )
                    )
            except Exception:
                logger.exception(
                    "projects.draft_conversion_failed",
                    item_id=item_id,
                    title=title,
                )

        logger.info("projects.fetched_items", count=len(items), project=project["title"])
        return items

    @staticmethod
    def _extract_status(node: dict) -> str:
        """Extract the Status field value from a project item node."""
        for fv in node["fieldValues"]["nodes"]:
            field = fv.get("field", {})
            if field.get("name") == "Status" and "name" in fv:
                return fv["name"]
        return ""

    async def load_field_metadata(self) -> None:
        """Load project field IDs and status options."""
        data = await self._github.graphql(
            QUERY_PROJECT_FIELDS,
            {
                "login": self._owner,
                "projectNumber": self._project_number,
            },
        )

        project = data["user"]["projectV2"]
        self._project_id = project["id"]

        for field in project["fields"]["nodes"]:
            if field.get("name") == "Status" and "options" in field:
                self._status_field_id = field["id"]
                self._status_options = {opt["name"]: opt["id"] for opt in field["options"]}
                logger.info(
                    "projects.loaded_status_options",
                    options=list(self._status_options.keys()),
                )

    async def _ensure_repo_id(self) -> str:
        """Fetch and cache the repository's GraphQL node ID."""
        if self._repo_id:
            return self._repo_id
        data = await self._github.graphql(
            QUERY_REPO_ID,
            {"owner": self._owner, "name": self._repo},
        )
        self._repo_id = data["repository"]["id"]
        return self._repo_id

    async def convert_draft_to_issue(self, item_id: str) -> dict | None:
        """Convert a DraftIssue project item into a real GitHub Issue.

        Returns the mutation result containing the new issue details,
        or None if the project ID is not yet known.
        """
        if not self._project_id:
            logger.warning("projects.convert_draft_no_project_id")
            return None

        repo_id = await self._ensure_repo_id()
        logger.info("projects.converting_draft", item_id=item_id)

        data = await self._github.graphql(
            MUTATION_CONVERT_DRAFT,
            {
                "itemId": item_id,
                "repositoryId": repo_id,
            },
        )
        result = data["convertProjectV2DraftIssueItemToIssue"]
        issue_number = result["item"]["content"]["number"]
        logger.info(
            "projects.draft_converted",
            item_id=item_id,
            issue_number=issue_number,
        )
        return result

    async def update_status(self, item_id: str, status_name: str) -> None:
        """Move a project item to a different status column.

        Performs case-insensitive matching against available options.
        """
        if not self._status_field_id:
            await self.load_field_metadata()

        # Case-insensitive lookup
        option_id = self._status_options.get(status_name)
        if not option_id:
            # Try case-insensitive match
            lower = status_name.lower()
            for name, oid in self._status_options.items():
                if name.lower() == lower:
                    option_id = oid
                    status_name = name  # use the canonical name
                    break

        if not option_id:
            raise ValueError(
                f"Unknown status '{status_name}'. "
                f"Available: {list(self._status_options.keys())}"
            )

        await self._github.graphql(
            MUTATION_UPDATE_STATUS,
            {
                "projectId": self._project_id,
                "itemId": item_id,
                "fieldId": self._status_field_id,
                "optionId": option_id,
            },
        )
        logger.info("projects.status_updated", item_id=item_id, status=status_name)
