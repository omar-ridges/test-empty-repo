"""GitHub issue lifecycle handling (LIFE-05).

Implements safe handling of GitHub issues that are closed (as completed or
as not planned) or deleted, both before a run starts and while a run is in
progress.

Guarantees:

* Unstarted work whose issue is closed or deleted is never claimed.
* A claim is re-verified after the claim action to shrink the race window
  between the state check and the claim itself.
* A run whose issue is closed or deleted mid-flight stops at the next
  checkpoint and is reported as cancelled instead of raising an error.
  (The exact mid-run policy is marked "TODO - discuss" upstream; stopping
  at the next checkpoint is the conservative default.)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

__all__ = [
    "IssueState",
    "CloseReason",
    "RunOutcome",
    "IssueSnapshot",
    "RunResult",
    "RunContext",
    "GitHubIssueError",
    "IssueNotFoundError",
    "IssueAborted",
    "GitHubIssueClient",
    "claim_issue_if_open",
    "run_issue_task",
]


class IssueState(str, Enum):
    """Lifecycle state of a GitHub issue."""

    OPEN = "open"
    CLOSED = "closed"
    DELETED = "deleted"


class CloseReason(str, Enum):
    """Reason an issue was closed, mirroring GitHub's ``state_reason``."""

    COMPLETED = "completed"
    NOT_PLANNED = "not_planned"


class RunOutcome(str, Enum):
    """Terminal outcome of a run over a single issue."""

    COMPLETED = "completed"
    SKIPPED_CLOSED = "skipped_closed"
    SKIPPED_DELETED = "skipped_deleted"
    CANCELLED_CLOSED = "cancelled_closed"
    CANCELLED_DELETED = "cancelled_deleted"
    FAILED = "failed"


class GitHubIssueError(RuntimeError):
    """Base error for GitHub issue operations."""


class IssueNotFoundError(GitHubIssueError):
    """The issue does not exist (deleted or never created).

    GitHub returns HTTP 404 for a deleted issue and HTTP 410 when an
    issue was removed; both map to this error so callers can treat
    "gone" uniformly and distinctly from other failures.
    """


class IssueAborted(RuntimeError):
    """Raised at a checkpoint when the issue is no longer actionable."""

    def __init__(self, state: IssueState, state_reason: Optional[str] = None):
        self.state = state
        self.state_reason = state_reason
        detail = f"issue is {state.value}"
        if state_reason:
            detail += f" ({state_reason})"
        super().__init__(detail)


@dataclass(frozen=True)
class IssueSnapshot:
    """Point-in-time view of an issue's lifecycle state."""

    number: int
    state: IssueState
    state_reason: Optional[str] = None
    title: str = ""

    @classmethod
    def from_api_payload(cls, payload: dict) -> "IssueSnapshot":
        try:
            state = IssueState(payload["state"])
        except (KeyError, ValueError) as exc:
            raise GitHubIssueError(
                f"unknown issue state in payload: {payload!r}"
            ) from exc
        return cls(
            number=int(payload["number"]),
            state=state,
            state_reason=payload.get("state_reason"),
            title=payload.get("title", ""),
        )

    @property
    def is_actionable(self) -> bool:
        """True only for open issues; closed/deleted work is not actionable."""
        return self.state is IssueState.OPEN


@dataclass
class RunResult:
    """Outcome of :func:`run_issue_task`."""

    outcome: RunOutcome
    detail: str = ""
    task_result: Any = None
    claimed: bool = False


Transport = Callable[[str, str, dict, Optional[str]], Any]


def _urllib_transport(method: str, url: str, headers: dict, body: Optional[str]):
    """Default transport built on the standard library."""
    data = body.encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network path
        return exc.code, exc.read()


class GitHubIssueClient:
    """Small GitHub issues client with a pluggable HTTP transport."""

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: str = "https://api.github.com",
        transport: Optional[Transport] = None,
    ):
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._transport = transport or _urllib_transport

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        url = f"{self._base_url}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "issue-lifecycle",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload = json.dumps(body) if body is not None else None
        if payload is not None:
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(method, url, headers, payload)
        data = json.loads(raw) if raw else None
        return status, data

    @staticmethod
    def _issue_path(repo: str, number: int) -> str:
        return f"/repos/{repo}/issues/{number}"

    def get_issue(self, repo: str, number: int) -> IssueSnapshot:
        """Fetch the current issue state.

        Raises :class:`IssueNotFoundError` for deleted issues (404/410)
        and :class:`GitHubIssueError` for any other failure.
        """
        status, data = self._request("GET", self._issue_path(repo, number))
        if status in (404, 410):
            raise IssueNotFoundError(f"issue #{number} in {repo} not found")
        if status >= 400:
            raise GitHubIssueError(f"GET issue failed with HTTP {status}")
        return IssueSnapshot.from_api_payload(data)

    def close_issue(
        self, repo: str, number: int, reason: CloseReason
    ) -> IssueSnapshot:
        """Close an issue as ``completed`` or ``not_planned``."""
        body = {"state": "closed", "state_reason": reason.value}
        status, data = self._request(
            "PATCH", self._issue_path(repo, number), body
        )
        if status in (404, 410):
            raise IssueNotFoundError(f"issue #{number} in {repo} not found")
        if status >= 400:
            raise GitHubIssueError(f"close issue failed with HTTP {status}")
        return IssueSnapshot.from_api_payload(data)

    def delete_issue(self, repo: str, number: int) -> bool:
        """Attempt to delete an issue.

        The GitHub REST API does not support deleting issues; a 405 is
        expected and reported as ``False``. A 204 means the deletion
        succeeded (e.g. via a proxy implementation). Deleted issues are
        indistinguishable from never-created ones via the API (404),
        which :meth:`get_issue` surfaces as :class:`IssueNotFoundError`.
        """
        status, _ = self._request("DELETE", self._issue_path(repo, number))
        if status == 204:
            return True
        if status in (404, 410):
            raise IssueNotFoundError(f"issue #{number} in {repo} not found")
        if status == 405:
            return False
        raise GitHubIssueError(f"delete issue failed with HTTP {status}")


def claim_issue_if_open(
    client: GitHubIssueClient,
    repo: str,
    number: int,
    claim_action: Callable[[str, int], None],
):
    """Claim an issue only if it is currently open.

    The state is checked *before* the claim action and re-verified
    *after* it, so an issue that is closed or deleted between the check
    and the claim is reported as not claimed. Returns a tuple of
    ``(claimed, snapshot)`` where ``snapshot`` is ``None`` when the issue
    was deleted.
    """
    try:
        before = client.get_issue(repo, number)
    except IssueNotFoundError:
        return False, None
    if not before.is_actionable:
        return False, before

    claim_action(repo, number)

    try:
        after = client.get_issue(repo, number)
    except IssueNotFoundError:
        return False, None
    if not after.is_actionable:
        return False, after
    return True, after


class RunContext:
    """Handed to the task so it can abort early via :meth:`checkpoint`."""

    def __init__(self, client: GitHubIssueClient, repo: str, number: int):
        self._client = client
        self.repo = repo
        self.number = number

    def checkpoint(self) -> IssueSnapshot:
        """Re-read the issue state; raise :class:`IssueAborted` if not open.

        Called by the task between units of work so that an issue closed
        or deleted mid-run stops gracefully instead of erroring.
        """
        try:
            snapshot = self._client.get_issue(self.repo, self.number)
        except IssueNotFoundError:
            raise IssueAborted(IssueState.DELETED) from None
        if not snapshot.is_actionable:
            raise IssueAborted(IssueState.CLOSED, snapshot.state_reason)
        return snapshot


def run_issue_task(
    client: GitHubIssueClient,
    repo: str,
    number: int,
    task: Callable[[RunContext], Any],
    claim_action: Optional[Callable[[str, int], None]] = None,
) -> RunResult:
    """Run ``task`` for an issue, respecting its lifecycle state.

    * Closed (completed or not planned) or deleted issues are never
      claimed and the task is never started.
    * If the issue is closed or deleted mid-run, the task's next
      :meth:`RunContext.checkpoint` aborts the run and the outcome is
      ``CANCELLED_CLOSED`` / ``CANCELLED_DELETED`` rather than an error.
    * Any other task exception yields ``FAILED`` and is contained.
    """
    claimed, snapshot = claim_issue_if_open(
        client, repo, number, claim_action or (lambda repo, number: None)
    )
    if not claimed:
        if snapshot is None:
            return RunResult(
                outcome=RunOutcome.SKIPPED_DELETED,
                detail=f"issue #{number} is deleted; unstarted work not claimed",
            )
        reason = snapshot.state_reason or "closed"
        return RunResult(
            outcome=RunOutcome.SKIPPED_CLOSED,
            detail=(
                f"issue #{number} is closed as {reason}; "
                "unstarted work not claimed"
            ),
        )

    context = RunContext(client, repo, number)
    try:
        result = task(context)
    except IssueAborted as exc:
        if exc.state is IssueState.DELETED:
            outcome = RunOutcome.CANCELLED_DELETED
        else:
            outcome = RunOutcome.CANCELLED_CLOSED
        return RunResult(
            outcome=outcome, detail=str(exc), claimed=True
        )
    except Exception as exc:  # noqa: BLE001 - contain all task failures
        return RunResult(
            outcome=RunOutcome.FAILED,
            detail=f"{type(exc).__name__}: {exc}",
            claimed=True,
        )
    return RunResult(
        outcome=RunOutcome.COMPLETED,
        task_result=result,
        claimed=True,
    )
