"""Claim logic for queued work (LIFE-05).

Unstarted work whose issue is closed (as completed or as not planned) or
deleted is never claimed. Transient lookup failures also withhold the claim:
work is only claimed when the issue is confirmed open.
"""

from __future__ import annotations

from .github_client import GitHubIssueLookup, TransientLookupError
from .models import IssueStatus, WorkItem, WorkItemState


_SKIP_REASONS = {
    IssueStatus.CLOSED_COMPLETED: "issue closed as completed",
    IssueStatus.CLOSED_NOT_PLANNED: "issue closed as not planned",
    IssueStatus.CLOSED: "issue closed",
    IssueStatus.DELETED: "issue deleted",
}


def skip_reason_for(status: IssueStatus) -> str:
    return _SKIP_REASONS.get(status, f"issue status {status.value}")


class ClaimService:
    """Claims unstarted work items after verifying their issue is still open."""

    def __init__(self, lookup: GitHubIssueLookup) -> None:
        self.lookup = lookup

    def claim(self, item: WorkItem) -> bool:
        """Attempt to claim an unstarted item.

        Returns ``True`` if the item was claimed. The item is skipped (never
        claimed) when its issue is closed — as completed or as not planned —
        or deleted. A transient lookup failure leaves the item unstarted so it
        can be retried on a later pass.
        """
        if not item.is_unstarted:
            return False

        status = self._resolve_status(item)

        if status is IssueStatus.OPEN:
            item.state = WorkItemState.CLAIMED
            item.skip_reason = None
            return True

        if status is IssueStatus.UNKNOWN:
            # State could not be determined (transient error): fail safe and
            # retry later rather than claiming unverified work.
            return False

        item.state = WorkItemState.SKIPPED
        item.skip_reason = skip_reason_for(status)
        return False

    def _resolve_status(self, item: WorkItem) -> IssueStatus:
        try:
            return self.lookup.get_issue_status(item.issue_number)
        except TransientLookupError:
            return IssueStatus.UNKNOWN
