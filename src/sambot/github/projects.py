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
        self._status_field_id: str | None = None
        self._status_options: dict[str, str] = {}

    async def get_items(self, first: int = 50) -> list[ProjectItem]:
        """Fetch items from the project board."""
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

        for node in project["items"]["nodes"]:
            content = node.get("content")
            if not content or "number" not in content:
                continue

            # Extract status from field values
            status = ""
            for fv in node["fieldValues"]["nodes"]:
                field = fv.get("field", {})
                if field.get("name") == "Status" and "name" in fv:
                    status = fv["name"]

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

        logger.info("projects.fetched_items", count=len(items), project=project["title"])
        return items

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

    async def update_status(self, item_id: str, status_name: str) -> None:
        """Move a project item to a different status column."""
        if not self._status_field_id:
            await self.load_field_metadata()

        option_id = self._status_options.get(status_name)
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
