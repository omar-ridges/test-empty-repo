"""Tests for LIFE-05: close/delete handling before and during a run."""

import time

import pytest

from issue_lifecycle import (
    ClaimError,
    IssueState,
    LifecycleWatcher,
    RunAborted,
    claim_issue,
)


def _provider(states):
    """Build a state provider backed by a dict of issue number -> IssueState."""
    return lambda number: states[number]


class TestPreExecution:
    """Before execution: unstarted closed/deleted work is never claimed."""

    @pytest.mark.parametrize(
        "state",
        [
            IssueState.CLOSED_COMPLETED,
            IssueState.CLOSED_NOT_PLANNED,
            IssueState.DELETED,
        ],
    )
    def test_unclaimable_states_raise(self, state):
        with pytest.raises(ClaimError) as exc:
            claim_issue(7, _provider({7: state}))
        assert exc.value.state is state

    def test_open_issue_is_claimed(self):
        issue = claim_issue(7, _provider({7: IssueState.OPEN}))
        assert issue.number == 7
        assert issue.state is IssueState.OPEN


class TestDuringRun:
    """During a run: closing/deleting the issue aborts the run."""

    @pytest.mark.parametrize(
        "state",
        [
            IssueState.CLOSED_COMPLETED,
            IssueState.CLOSED_NOT_PLANNED,
            IssueState.DELETED,
        ],
    )
    def test_mid_run_close_delete_aborts(self, state):
        states = {7: IssueState.OPEN}
        watcher = LifecycleWatcher(7, _provider(states), interval=0.01)
        watcher.check()  # open at start: no abort
        states[7] = state  # issue closed/deleted mid-run
        with pytest.raises(RunAborted) as exc:
            watcher.check()
        assert exc.value.state is state

    def test_background_poller_stops_cleanly(self):
        states = {7: IssueState.OPEN}
        watcher = LifecycleWatcher(7, _provider(states), interval=0.01)
        watcher.start()
        time.sleep(0.05)  # let it poll while open
        watcher.stop()
        assert not watcher._thread or not watcher._thread.is_alive()

    def test_background_poller_surfaces_abort(self):
        states = {7: IssueState.OPEN}
        watcher = LifecycleWatcher(7, _provider(states), interval=0.01)
        watcher.start()
        states[7] = IssueState.DELETED
        deadline = time.monotonic() + 2.0
        while watcher._thread and watcher._thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(RunAborted):
            watcher.check()
        watcher.stop()


class TestMapping:
    def test_state_reason_mapping(self):
        assert IssueState.from_api("closed", "completed") is IssueState.CLOSED_COMPLETED
        assert IssueState.from_api("closed", "not_planned") is IssueState.CLOSED_NOT_PLANNED
        assert IssueState.from_api("closed", None) is IssueState.CLOSED_COMPLETED
        assert IssueState.from_api("open") is IssueState.OPEN
