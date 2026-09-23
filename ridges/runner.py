"""Run orchestration with issue lifecycle checks (LIFE-05).

The issue state is re-checked immediately before each unstarted item is
claimed. A single mechanism therefore covers both timings required by LIFE-05:

* **before execution** — the first check happens before any work starts;
* **during a run** — every subsequent item is re-checked at claim time, so
  issues closed or deleted after the run began are still not claimed.

Already claimed/started items are allowed to finish: LIFE-05 marks the fate of
in-flight work as "TODO - discuss", and the conservative reading is that only
*unstarted* work is withheld from claiming.
"""

from __future__ import annotations

from .claimer import ClaimService
from .github_client import GitHubIssueLookup
from .models import WorkItem, WorkItemState


class RunOrchestrator:
    """Processes a queue of work items tied to GitHub issues."""

    def __init__(self, lookup: GitHubIssueLookup) -> None:
        self.claims = ClaimService(lookup)
        self.executed: list[int] = []

    def run(self, queue: list[WorkItem]) -> list[WorkItem]:
        """Claim and execute claimable items; skip the rest.

        Returns the items that were processed to completion. Items whose
        issues were closed or deleted are left in ``SKIPPED`` state with a
        ``skip_reason`` and produce no claim signal (no execution, no
        in-progress state).
        """
        processed: list[WorkItem] = []
        for item in queue:
            if item.state is WorkItemState.UNSTARTED and not self.claims.claim(item):
                continue
            if item.state in (WorkItemState.CLAIMED, WorkItemState.STARTED):
                self._execute(item)
                processed.append(item)
        return processed

    def _execute(self, item: WorkItem) -> None:
        item.state = WorkItemState.STARTED
        self.executed.append(item.issue_number)
        # Real execution hook goes here; the run completes the item.
        item.state = WorkItemState.COMPLETED
