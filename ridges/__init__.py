"""Issue lifecycle guard: never claim unstarted work whose GitHub issue is closed or deleted.

Implements LIFE-05.
"""

from .claimer import ClaimService
from .github_client import (
    GitHubIssueClient,
    GitHubIssueLookup,
    TransientLookupError,
    issue_status_from_payload,
)
from .models import IssueStatus, WorkItem, WorkItemState
from .runner import RunOrchestrator

__all__ = [
    "ClaimService",
    "GitHubIssueClient",
    "GitHubIssueLookup",
    "IssueStatus",
    "RunOrchestrator",
    "TransientLookupError",
    "WorkItem",
    "WorkItemState",
    "issue_status_from_payload",
]
