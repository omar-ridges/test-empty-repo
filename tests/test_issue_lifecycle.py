"""Tests for LIFE-05 issue lifecycle handling."""

import json
import unittest

from issue_lifecycle import (
    CloseReason,
    GitHubIssueClient,
    GitHubIssueError,
    IssueNotFoundError,
    IssueSnapshot,
    IssueState,
    RunOutcome,
    claim_issue_if_open,
    run_issue_task,
)


def issue_payload(number=1, state="open", state_reason=None, title="t"):
    return {
        "number": number,
        "state": state,
        "state_reason": state_reason,
        "title": title,
    }


class FakeTransport:
    """Scripted transport: pops one (status, payload) response per request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, method, url, headers, body):
        if not self.responses:
            raise AssertionError("unexpected extra request: %s %s" % (method, url))
        status, payload = self.responses.pop(0)
        self.requests.append((method, url, headers, body))
        raw = json.dumps(payload).encode("utf-8") if payload is not None else b""
        return status, raw


def make_client(responses, token="tok"):
    transport = FakeTransport(responses)
    return GitHubIssueClient(token=token, transport=transport), transport


class TestIssueSnapshot(unittest.TestCase):
    def test_open_is_actionable(self):
        snap = IssueSnapshot.from_api_payload(issue_payload())
        self.assertEqual(snap.state, IssueState.OPEN)
        self.assertTrue(snap.is_actionable)

    def test_closed_completed_not_actionable(self):
        snap = IssueSnapshot.from_api_payload(
            issue_payload(state="closed", state_reason="completed")
        )
        self.assertEqual(snap.state, IssueState.CLOSED)
        self.assertEqual(snap.state_reason, "completed")
        self.assertFalse(snap.is_actionable)

    def test_closed_not_planned_not_actionable(self):
        snap = IssueSnapshot.from_api_payload(
            issue_payload(state="closed", state_reason="not_planned")
        )
        self.assertEqual(snap.state_reason, "not_planned")
        self.assertFalse(snap.is_actionable)

    def test_unknown_state_raises(self):
        with self.assertRaises(GitHubIssueError):
            IssueSnapshot.from_api_payload(issue_payload(state="locked"))


class TestGitHubIssueClient(unittest.TestCase):
    def test_get_issue_ok(self):
        client, transport = make_client([(200, issue_payload(7))])
        snap = client.get_issue("o/r", 7)
        self.assertEqual(snap.number, 7)
        method, url, headers, _ = transport.requests[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.endswith("/repos/o/r/issues/7"))
        self.assertEqual(headers["Authorization"], "Bearer tok")

    def test_get_issue_deleted_404(self):
        client, _ = make_client([(404, {"message": "Not Found"})])
        with self.assertRaises(IssueNotFoundError):
            client.get_issue("o/r", 1)

    def test_get_issue_gone_410(self):
        client, _ = make_client([(410, {"message": "Gone"})])
        with self.assertRaises(IssueNotFoundError):
            client.get_issue("o/r", 1)

    def test_get_issue_server_error(self):
        client, _ = make_client([(500, {"message": "boom"})])
        with self.assertRaises(GitHubIssueError) as ctx:
            client.get_issue("o/r", 1)
        self.assertNotIsInstance(ctx.exception, IssueNotFoundError)

    def test_close_issue_completed_sends_state_reason(self):
        client, transport = make_client(
            [(200, issue_payload(state="closed", state_reason="completed"))]
        )
        snap = client.close_issue("o/r", 3, CloseReason.COMPLETED)
        self.assertEqual(snap.state, IssueState.CLOSED)
        method, _, _, body = transport.requests[0]
        self.assertEqual(method, "PATCH")
        self.assertEqual(json.loads(body), {"state": "closed", "state_reason": "completed"})

    def test_close_issue_not_planned(self):
        client, transport = make_client(
            [(200, issue_payload(state="closed", state_reason="not_planned"))]
        )
        client.close_issue("o/r", 3, CloseReason.NOT_PLANNED)
        self.assertEqual(
            json.loads(transport.requests[0][3])["state_reason"], "not_planned"
        )

    def test_delete_issue_unsupported_405(self):
        client, transport = make_client([(405, {"message": "not allowed"})])
        self.assertFalse(client.delete_issue("o/r", 1))
        self.assertEqual(transport.requests[0][0], "DELETE")

    def test_delete_issue_ok_204(self):
        client, _ = make_client([(204, None)])
        self.assertTrue(client.delete_issue("o/r", 1))

    def test_delete_issue_missing_404(self):
        client, _ = make_client([(404, {"message": "Not Found"})])
        with self.assertRaises(IssueNotFoundError):
            client.delete_issue("o/r", 1)


class TestClaimLogic(unittest.TestCase):
    def test_open_issue_is_claimed(self):
        client, _ = make_client([(200, issue_payload()), (200, issue_payload())])
        claims = []
        claimed, snap = claim_issue_if_open(
            client, "o/r", 1, lambda repo, n: claims.append((repo, n))
        )
        self.assertTrue(claimed)
        self.assertTrue(snap.is_actionable)
        self.assertEqual(claims, [("o/r", 1)])

    def test_closed_completed_not_claimed(self):
        client, _ = make_client(
            [(200, issue_payload(state="closed", state_reason="completed"))]
        )
        claims = []
        claimed, snap = claim_issue_if_open(
            client, "o/r", 1, lambda repo, n: claims.append(n)
        )
        self.assertFalse(claimed)
        self.assertEqual(claims, [])
        self.assertEqual(snap.state_reason, "completed")

    def test_closed_not_planned_not_claimed(self):
        client, _ = make_client(
            [(200, issue_payload(state="closed", state_reason="not_planned"))]
        )
        claimed, _ = claim_issue_if_open(client, "o/r", 1, lambda repo, n: None)
        self.assertFalse(claimed)

    def test_deleted_issue_not_claimed(self):
        client, _ = make_client([(404, {"message": "Not Found"})])
        claims = []
        claimed, snap = claim_issue_if_open(
            client, "o/r", 1, lambda repo, n: claims.append(n)
        )
        self.assertFalse(claimed)
        self.assertIsNone(snap)
        self.assertEqual(claims, [])

    def test_race_closed_between_check_and_claim(self):
        client, _ = make_client(
            [(200, issue_payload()), (200, issue_payload(state="closed"))]
        )
        claims = []
        claimed, _ = claim_issue_if_open(
            client, "o/r", 1, lambda repo, n: claims.append(n)
        )
        self.assertFalse(claimed)
        self.assertEqual(claims, [1])  # claim ran, but is reported unclaimed

    def test_race_deleted_after_claim(self):
        client, _ = make_client([(200, issue_payload()), (404, {"message": "NF"})])
        claimed, snap = claim_issue_if_open(client, "o/r", 1, lambda repo, n: None)
        self.assertFalse(claimed)
        self.assertIsNone(snap)


class TestRunLifecycle(unittest.TestCase):
    def test_happy_path(self):
        client, _ = make_client(
            [(200, issue_payload()), (200, issue_payload()), (200, issue_payload())]
        )

        def task(ctx):
            ctx.checkpoint()
            return 42

        result = run_issue_task(client, "o/r", 1, task)
        self.assertEqual(result.outcome, RunOutcome.COMPLETED)
        self.assertEqual(result.task_result, 42)
        self.assertTrue(result.claimed)

    def test_unstarted_closed_completed_is_skipped(self):
        client, _ = make_client(
            [(200, issue_payload(state="closed", state_reason="completed"))]
        )
        ran = []
        result = run_issue_task(client, "o/r", 1, lambda ctx: ran.append(1))
        self.assertEqual(result.outcome, RunOutcome.SKIPPED_CLOSED)
        self.assertIn("completed", result.detail)
        self.assertFalse(result.claimed)
        self.assertEqual(ran, [])

    def test_unstarted_closed_not_planned_is_skipped(self):
        client, _ = make_client(
            [(200, issue_payload(state="closed", state_reason="not_planned"))]
        )
        ran = []
        result = run_issue_task(client, "o/r", 1, lambda ctx: ran.append(1))
        self.assertEqual(result.outcome, RunOutcome.SKIPPED_CLOSED)
        self.assertIn("not_planned", result.detail)
        self.assertEqual(ran, [])

    def test_unstarted_deleted_is_skipped(self):
        client, _ = make_client([(404, {"message": "Not Found"})])
        ran = []
        result = run_issue_task(client, "o/r", 1, lambda ctx: ran.append(1))
        self.assertEqual(result.outcome, RunOutcome.SKIPPED_DELETED)
        self.assertFalse(result.claimed)
        self.assertEqual(ran, [])

    def test_closed_mid_run_cancels_gracefully(self):
        client, _ = make_client(
            [
                (200, issue_payload()),
                (200, issue_payload()),
                (200, issue_payload(state="closed", state_reason="not_planned")),
            ]
        )

        def task(ctx):
            ctx.checkpoint()
            return "never"

        result = run_issue_task(client, "o/r", 1, task)
        self.assertEqual(result.outcome, RunOutcome.CANCELLED_CLOSED)
        self.assertIn("not_planned", result.detail)
        self.assertTrue(result.claimed)

    def test_deleted_mid_run_cancels_gracefully(self):
        client, _ = make_client(
            [(200, issue_payload()), (200, issue_payload()), (404, {"message": "NF"})]
        )

        def task(ctx):
            ctx.checkpoint()
            return "never"

        result = run_issue_task(client, "o/r", 1, task)
        self.assertEqual(result.outcome, RunOutcome.CANCELLED_DELETED)
        self.assertTrue(result.claimed)

    def test_task_failure_is_contained(self):
        client, _ = make_client([(200, issue_payload()), (200, issue_payload())])

        def task(ctx):
            raise ValueError("boom")

        result = run_issue_task(client, "o/r", 1, task)
        self.assertEqual(result.outcome, RunOutcome.FAILED)
        self.assertIn("boom", result.detail)
        self.assertTrue(result.claimed)


if __name__ == "__main__":
    unittest.main()
