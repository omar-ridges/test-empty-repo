"""Run-time lifecycle watcher: abort a run whose issue closes/deletes mid-run."""

from __future__ import annotations

import threading
from typing import Callable

from .models import IssueState


class RunAborted(RuntimeError):
    """Raised when a run must stop because its issue closed or was deleted."""

    def __init__(self, issue_number: int, state: IssueState) -> None:
        self.issue_number = issue_number
        self.state = state
        super().__init__(
            f"run aborted: issue #{issue_number} became {state.value} during the run"
        )


class LifecycleWatcher:
    """Polls issue state during a run and aborts if the issue is no longer open.

    ``check`` raises :class:`RunAborted` when the tracked issue has been closed
    (completed or not planned) or deleted since the run started. Runs already
    in progress are allowed to finish their current unit of work before the
    abort surfaces; the check is cooperative, called at safe points.
    """

    def __init__(
        self,
        issue_number: int,
        provider: Callable[[int], IssueState],
        interval: float = 30.0,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._issue_number = issue_number
        self._provider = provider
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def issue_number(self) -> int:
        return self._issue_number

    def check(self) -> None:
        """One-shot cooperative check; call at safe points inside a run."""
        state = self._provider(self._issue_number)
        if not state.is_claimable:
            raise RunAborted(self._issue_number, state)

    def start(self) -> None:
        """Start background polling (daemon thread) that calls ``check``."""
        if self._thread is not None:
            raise RuntimeError("watcher already started")

        def _loop() -> None:
            while not self._stop.wait(self._interval):
                try:
                    self.check()
                except RunAborted:
                    # Abort surfaces on the next cooperative check()/result
                    # retrieval; the background loop must not crash loudly.
                    self._stop.set()

        self._thread = threading.Thread(target=_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the background poller to stop."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
