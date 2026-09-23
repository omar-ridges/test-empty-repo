"""Tests for LIFE-05: closed/deleted issues are never claimed."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ridges.claimer import ClaimService
from ridges.github_client import (
    GitHubIssueClient,
    TransientLookupError,
    issue_status_from_payload,
)
from ridges.models import IssueStatus, WorkItem, WorkItemState
from ridges.runner import RunOrchestrator


class FakeGitHubIssueLookup:
    """In-memory lookup with programmable statuses."""

    def __init__(self, statuses=None):
        self.statuses = dict(statuses or {})
        self.calls = []

    def set_status(self, issue_number, status):
        self.statuses[issue_number] = status

    def get_issue_status(self, issue_number):
        self.calls.append(issue_number)
        status = self.statuses.get(issue_number, IssueStatus.OPEN)
        if isinstance(status, Exception):
            raise status
        return status


class DegradesAfterFirstRead:
    """Returns OPEN on the first lookup, then a worse status on every later
    lookup.

    Simulates an issue being closed/deleted *while a run is in progress*: the
    first item's check (before execution) sees the issue open, and the check
    for every subsequent item — made after earlier items were processed —
    sees the degraded status.
    """

    def __init__(self, later_status):
        self.later_status = later_status
        self.calls = 0

    def get_issue_status(self, issue_number):
        self.calls += 1
        return IssueStatus.OPEN if self.calls == 1 else self.later_status


# ---------------------------------------------------------------------------
# Claim decisions before execution
# ---------------------------------------------------------------------------


def test_open_issue_is_claimed_and_executed():
    orchestrator = RunOrchestrator(FakeGitHubIssueLookup())
    queue = [WorkItem(issue_number=1)]

    processed = orchestrator.run(queue)

    assert [item.issue_number for item in processed] == [1]
    assert queue[0].state is WorkItemState.COMPLETED
    assert orchestrator.executed == [1]


@pytest.mark.parametrize(
    "status",
    [
        IssueStatus.CLOSED_COMPLETED,
        IssueStatus.CLOSED_NOT_PLANNED,
        IssueStatus.CLOSED,
        IssueStatus.DELETED,
    ],
)
def test_closed_or_deleted_issue_is_not_claimed(status):
    lookup = FakeGitHubIssueLookup({1: status})
    item = WorkItem(issue_number=1)

    claimed = ClaimService(lookup).claim(item)

    assert claimed is False
    assert item.state is WorkItemState.SKIPPED
    assert item.skip_reason


@pytest.mark.parametrize(
    "status,expected_reason",
    [
        (IssueStatus.CLOSED_COMPLETED, "issue closed as completed"),
        (IssueStatus.CLOSED_NOT_PLANNED, "issue closed as not planned"),
        (IssueStatus.DELETED, "issue deleted"),
    ],
)
def test_skip_reason_describes_the_lifecycle_change(status, expected_reason):
    item = WorkItem(issue_number=7)

    ClaimService(FakeGitHubIssueLookup({7: status})).claim(item)

    assert item.skip_reason == expected_reason


def test_transient_lookup_error_leaves_item_unstarted():
    lookup = FakeGitHubIssueLookup({1: TransientLookupError("rate limited")})
    item = WorkItem(issue_number=1)

    claimed = ClaimService(lookup).claim(item)

    assert claimed is False
    # Not skipped either: it stays unstarted so a later pass can retry it.
    assert item.state is WorkItemState.UNSTARTED
    assert item.skip_reason is None


# ---------------------------------------------------------------------------
# Lifecycle changes during a run
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "later_status",
    [
        IssueStatus.CLOSED_COMPLETED,
        IssueStatus.CLOSED_NOT_PLANNED,
        IssueStatus.DELETED,
    ],
)
def test_issue_closed_or_deleted_during_run_is_not_claimed(later_status):
    # Issue 1 is open when the run starts; issue 2 is closed/deleted while
    # item 1 is being processed, i.e. before item 2 is claimed.
    lookup = DegradesAfterFirstRead(later_status)
    orchestrator = RunOrchestrator(lookup)
    queue = [WorkItem(issue_number=1), WorkItem(issue_number=2)]

    processed = orchestrator.run(queue)

    assert [item.issue_number for item in processed] == [1]
    assert queue[0].state is WorkItemState.COMPLETED
    assert queue[1].state is WorkItemState.SKIPPED
    assert queue[1].skip_reason
    assert orchestrator.executed == [1]


def test_started_work_finishes_even_if_issue_closes_mid_run():
    # LIFE-05 leaves in-flight work as "TODO - discuss"; only unstarted work
    # is withheld. An item claimed while the issue was open runs to completion.
    lookup = DegradesAfterFirstRead(IssueStatus.CLOSED_COMPLETED)
    orchestrator = RunOrchestrator(lookup)
    queue = [WorkItem(issue_number=1)]

    processed = orchestrator.run(queue)

    assert [item.issue_number for item in processed] == [1]
    assert queue[0].state is WorkItemState.COMPLETED


def test_skipped_items_produce_no_claim_signal():
    lookup = FakeGitHubIssueLookup({1: IssueStatus.DELETED})
    orchestrator = RunOrchestrator(lookup)
    queue = [WorkItem(issue_number=1)]

    processed = orchestrator.run(queue)

    assert processed == []
    assert orchestrator.executed == []  # no execution / in-progress state
    assert queue[0].state is WorkItemState.SKIPPED
    assert queue[0].skip_reason == "issue deleted"


# ---------------------------------------------------------------------------
# Payload mapping and the real client (404/410 => deleted)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"state": "open"}, IssueStatus.OPEN),
        ({"state": "closed", "state_reason": "completed"}, IssueStatus.CLOSED_COMPLETED),
        ({"state": "closed", "state_reason": "not_planned"}, IssueStatus.CLOSED_NOT_PLANNED),
        ({"state": "closed", "state_reason": None}, IssueStatus.CLOSED),
        ({"state": "closed"}, IssueStatus.CLOSED),
        ({"state": "open", "pull_request": {"url": "x"}}, IssueStatus.UNKNOWN),
        ({}, IssueStatus.UNKNOWN),
    ],
)
def test_issue_status_from_payload(payload, expected):
    assert issue_status_from_payload(payload) is expected


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.endswith("/issues/404"):
            self.send_response(404)
            self.end_headers()
        elif self.path.endswith("/issues/410"):
            self.send_response(410)
            self.end_headers()
        else:
            body = json.dumps({"state": "open", "state_reason": None}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *args):  # silence test output
        pass


@pytest.fixture()
def api_base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_client_maps_404_and_410_to_deleted(api_base):
    client = GitHubIssueClient(repo="owner/repo", api_base=api_base)

    assert client.get_issue_status(404) is IssueStatus.DELETED
    assert client.get_issue_status(410) is IssueStatus.DELETED


def test_client_maps_open_issue(api_base):
    client = GitHubIssueClient(repo="owner/repo", api_base=api_base)

    assert client.get_issue_status(1) is IssueStatus.OPEN


def test_client_maps_server_error_to_transient():
    # 500 is not treated as deleted; it must surface as a transient failure so
    # the claimer withholds the claim instead of skipping or claiming.
    import urllib.error
    from unittest.mock import patch

    client = GitHubIssueClient(repo="owner/repo", api_base="http://unused")

    with patch("ridges.github_client.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="x", code=500, msg="oops", hdrs=None, fp=None
        )
        with pytest.raises(TransientLookupError):
            client.get_issue_status(1)
