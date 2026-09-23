"""Data models for GitHub issue lifecycle handling (LIFE-05)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class IssueStatus(str, Enum):
    """Lifecycle status of a GitHub issue as observed through the API.

    GitHub distinguishes closed issues via ``state_reason``:
    ``completed`` vs ``not_planned``. Deleted issues are not returned at all
    (the API responds 404/410) and are modelled as ``DELETED``.
    """

    OPEN = "open"
    CLOSED_COMPLETED = "closed_completed"
    CLOSED_NOT_PLANNED = "closed_not_planned"
    CLOSED = "closed"  # closed with no state_reason
    DELETED = "deleted"
    UNKNOWN = "unknown"

    @property
    def is_claimable(self) -> bool:
        """Only open issues represent claimable work."""
        return self is IssueStatus.OPEN


class WorkItemState(str, Enum):
    """State of a queued work item in the run pipeline."""

    UNSTARTED = "unstarted"
    CLAIMED = "claimed"
    STARTED = "started"
    SKIPPED = "skipped"
    COMPLETED = "completed"


@dataclass
class WorkItem:
    """A unit of queued work tied to a GitHub issue."""

    issue_number: int
    state: WorkItemState = WorkItemState.UNSTARTED
    skip_reason: Optional[str] = None

    @property
    def is_unstarted(self) -> bool:
        return self.state is WorkItemState.UNSTARTED
