"""GitHub issue lookup with deletion detection (LIFE-05).

Deleted issues are not returned by the GitHub API: the endpoint responds with
HTTP 404 (Not Found) or 410 (Gone). Both are mapped to ``IssueStatus.DELETED``
so callers treat them as "do not claim".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from .models import IssueStatus


class GitHubIssueLookup(Protocol):
    """Anything that can resolve the current lifecycle status of an issue."""

    def get_issue_status(self, issue_number: int) -> IssueStatus:
        ...  # pragma: no cover


class TransientLookupError(Exception):
    """The issue status could not be determined due to a transient error."""


def issue_status_from_payload(payload: dict) -> IssueStatus:
    """Map a GitHub issue API payload to an :class:`IssueStatus`."""
    # The issues endpoint also serves pull requests; they are not work items.
    if payload.get("pull_request") is not None:
        return IssueStatus.UNKNOWN

    state = payload.get("state")
    if state == "open":
        return IssueStatus.OPEN
    if state == "closed":
        reason = payload.get("state_reason")
        if reason == "completed":
            return IssueStatus.CLOSED_COMPLETED
        if reason == "not_planned":
            return IssueStatus.CLOSED_NOT_PLANNED
        return IssueStatus.CLOSED
    return IssueStatus.UNKNOWN


class GitHubIssueClient:
    """Concrete lookup backed by the GitHub REST API."""

    def __init__(
        self,
        repo: str,
        token: str | None = None,
        api_base: str = "https://api.github.com",
    ) -> None:
        self.repo = repo
        self.token = token
        self.api_base = api_base.rstrip("/")

    def get_issue_status(self, issue_number: int) -> IssueStatus:
        url = f"{self.api_base}/repos/{self.repo}/issues/{issue_number}"
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json"}
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(request) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                # The issue was deleted (or is inaccessible): never claim it.
                return IssueStatus.DELETED
            raise TransientLookupError(
                f"HTTP {exc.code} looking up issue #{issue_number}"
            ) from exc
        except urllib.error.URLError as exc:
            raise TransientLookupError(
                f"network error looking up issue #{issue_number}: {exc.reason}"
            ) from exc

        try:
            payload = json.loads(body)
        except (ValueError, TypeError) as exc:
            raise TransientLookupError(
                f"malformed response looking up issue #{issue_number}"
            ) from exc

        return issue_status_from_payload(payload)
