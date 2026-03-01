"""GitHub REST and GraphQL client."""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

import httpx
from github import Auth, Github

if TYPE_CHECKING:
    from sambot.config import Settings


class GitHubClient:
    """Wrapper around PyGitHub (REST) and httpx (GraphQL)."""

    GRAPHQL_URL = "https://api.github.com/graphql"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token = settings.github_token

    @cached_property
    def rest(self) -> Github:
        """PyGitHub client for REST API operations."""
        auth = Auth.Token(self._token)
        return Github(auth=auth)

    @cached_property
    def repo(self):
        """Get the target repository."""
        return self.rest.get_repo(self._settings.github_repo)

    async def graphql(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query against GitHub's API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self.GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data["data"]

    def graphql_sync(self, query: str, variables: dict | None = None) -> dict:
        """Execute a GraphQL query synchronously."""
        with httpx.Client() as client:
            response = client.post(
                self.GRAPHQL_URL,
                json={"query": query, "variables": variables or {}},
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            if "errors" in data:
                raise RuntimeError(f"GraphQL errors: {data['errors']}")
            return data["data"]

    def close(self) -> None:
        """Clean up resources."""
        self.rest.close()
