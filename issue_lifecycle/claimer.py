"""Claiming gate: unstarted closed/deleted work must never be claimed."""

from __future__ import annotations

from typing import Callable

from .models import Issue, IssueState


# A provider fetches the *current* lifecycle state of an issue by number.
# Implementations typically wrap the GitHub REST/GraphQL API. A provider
# returning IssueState.DELETED is how a 404/NOT_FOUND is surfaced.
StateProvider = Callable[[int], IssueState]


class ClaimError(RuntimeError):
    """Raised when an issue cannot be claimed for a run."""

    def __init__(self, issue_number: int, state: IssueState) -> None:
        self.issue_number = issue_number
        self.state = state
        super().__init__(
            f"issue #{issue_number} cannot be claimed: state is {state.value}"
        )


def claim_issue(
    issue_number: int,
    provider: StateProvider,
) -> Issue:
    """Claim an issue for a run, refusing any closed/deleted work.

    This is the pre-execution gate: even if a stale cache/queue still lists a
    closed or deleted issue as available work, the claim re-checks live state
    and raises :class:`ClaimError` for anything that is not OPEN.
    """
    state = provider(issue_number)
    if not state.is_claimable:
        raise ClaimError(issue_number, state)
    return Issue(number=issue_number, state=state)
