"""Issue lifecycle handling (LIFE-05): close-as-completed, close-as-not-planned,
and delete, enforced both before claiming and during an active run."""

from .models import Issue, IssueState
from .claimer import claim_issue, ClaimError
from .watcher import LifecycleWatcher, RunAborted

__all__ = [
    "Issue",
    "IssueState",
    "claim_issue",
    "ClaimError",
    "LifecycleWatcher",
    "RunAborted",
]
