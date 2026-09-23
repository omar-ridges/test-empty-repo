"""Core models for issue lifecycle tracking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class IssueState(str, Enum):
    """Lifecycle states of a GitHub issue relevant to claiming and running.

    GitHub represents "close as completed" / "close as not planned" via the
    ``state_reason`` field on the REST API ("completed" / "not_planned").
    Deletion is only possible via the GraphQL ``deleteIssue`` mutation; a
    deleted issue surfaces to clients as 404 / NOT_FOUND, which we model as
    the DELETED state.
    """

    OPEN = "open"
    CLOSED_COMPLETED = "closed_completed"
    CLOSED_NOT_PLANNED = "closed_not_planned"
    DELETED = "deleted"

    @classmethod
    def from_api(cls, state: str, state_reason: str | None = None) -> "IssueState":
        """Map a REST API (state, state_reason) pair to an IssueState."""
        if state == "closed":
            if state_reason == "not_planned":
                return cls.CLOSED_NOT_PLANNED
            return cls.CLOSED_COMPLETED
        return cls.OPEN

    @property
    def is_claimable(self) -> bool:
        """Only open issues may be claimed; closed/deleted work is never claimable."""
        return self is IssueState.OPEN


@dataclass(frozen=True)
class Issue:
    """A minimal snapshot of a GitHub issue's identity and lifecycle state."""

    number: int
    state: IssueState

    def __post_init__(self) -> None:
        if self.number <= 0:
            raise ValueError("issue number must be a positive integer")
