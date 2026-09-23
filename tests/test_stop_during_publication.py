"""LIFE-03: Stop during patch upload or PR publication.

Covers the acceptance criteria:

- Stop is available during patch upload and PR publication (assisted flow).
- Exactly one clear final outcome (stopped / published / failed).
- No duplicate charge: stopped and failed sessions are never charged, a
  published session is charged exactly once.
- Truthful UI when the PR was already published.
- The point after which Stop cannot prevent publication is explicit.

Runs under pytest (``python -m pytest tests -q``) and standalone
(``python tests/test_stop_during_publication.py``) with no dependencies.
"""

import os
import sys
import threading
import time
import traceback
from typing import Callable, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from publication import (
    BillingLedger,
    DuplicateChargeError,
    FinalOutcome,
    InvalidTransitionError,
    Phase,
    PublicationSession,
    PublicationStopped,
    render_status,
)


class FakeRemote:
    """Scriptable stand-in for the patch/PR remote."""

    def __init__(self) -> None:
        self.uploads: List[bytes] = []
        self.pr_creations: List[str] = []
        self.upload_error: Optional[Exception] = None
        self.create_error: Optional[Exception] = None

    def upload(self, patch: bytes) -> str:
        if self.upload_error is not None:
            raise self.upload_error
        self.uploads.append(patch)
        return f"tok-{len(self.uploads)}"

    def create_pr(self, token: str, metadata: dict) -> str:
        if self.create_error is not None:
            raise self.create_error
        url = f"https://pr.example/{token}"
        self.pr_creations.append(url)
        return url


def make_session(
    remote: FakeRemote,
    session_id: str = "s",
    amount_cents: int = 1000,
    billing: Optional[BillingLedger] = None,
    upload_fn: Optional[Callable[[bytes], str]] = None,
    create_fn: Optional[Callable[[str, dict], str]] = None,
) -> PublicationSession:
    return PublicationSession(
        session_id,
        upload_patch_fn=upload_fn if upload_fn is not None else remote.upload,
        create_pr_fn=create_fn if create_fn is not None else remote.create_pr,
        billing=billing if billing is not None else BillingLedger(),
        amount_cents=amount_cents,
    )


def wait_for_phase(session: PublicationSession, phase: Phase, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if session.phase is phase:
            return True
        time.sleep(0.005)
    return False


# --------------------------------------------------------------------------
# The agreed point of no return
# --------------------------------------------------------------------------


def test_point_of_no_return_is_explicit():
    text = PublicationSession.POINT_OF_NO_RETURN
    assert "PUBLISHING" in text
    assert "Stop" in text


def test_status_before_point_of_no_return_allows_stop():
    remote = FakeRemote()
    session = make_session(remote)
    session.upload_patch(b"patch")
    status = session.status()
    assert status.point_of_no_return_passed is False
    assert status.final_outcome is None
    text = render_status(session)
    assert "stop" in text.lower()


# --------------------------------------------------------------------------
# Stop during patch upload
# --------------------------------------------------------------------------


def test_stop_before_upload_prevents_publication_and_charge():
    remote = FakeRemote()
    session = make_session(remote)
    result = session.request_stop()
    assert result.honoured is True
    outcome = session.run(b"patch")
    assert outcome is FinalOutcome.STOPPED
    assert remote.uploads == []
    assert remote.pr_creations == []
    assert session.has_charge() is False
    assert session.final_outcome is FinalOutcome.STOPPED


def test_stop_during_patch_upload_takes_effect_before_publication():
    remote = FakeRemote()
    holder = {}

    def upload_with_stop(patch: bytes) -> str:
        result = holder["session"].request_stop()  # Stop clicked mid-upload
        assert result.honoured is True
        return remote.upload(patch)  # upload itself completes

    session = make_session(
        remote, session_id="s-up", amount_cents=2500, upload_fn=upload_with_stop
    )
    holder["session"] = session
    outcome = session.run(b"patch")
    assert outcome is FinalOutcome.STOPPED
    assert remote.uploads == [b"patch"]      # upload happened...
    assert remote.pr_creations == []         # ...but nothing was published
    assert session.has_charge() is False     # and nothing was charged
    assert render_status(session).startswith("Stopped")


def test_stop_between_upload_and_publish_prevents_publication():
    remote = FakeRemote()
    session = make_session(remote)
    session.upload_patch(b"patch")
    assert session.phase is Phase.UPLOAD_COMPLETE
    result = session.request_stop()
    assert result.honoured is True
    try:
        session.publish()
        raised = False
    except PublicationStopped:
        raised = True
    assert raised is True
    assert session.final_outcome is FinalOutcome.STOPPED
    assert remote.pr_creations == []
    assert session.has_charge() is False


# --------------------------------------------------------------------------
# Stop during PR publication (past the point of no return)
# --------------------------------------------------------------------------


def test_stop_during_publication_cannot_prevent_publishing_truthful_ui():
    remote = FakeRemote()
    billing = BillingLedger()
    holder = {}

    def create_with_stop(token: str, metadata: dict) -> str:
        result = holder["session"].request_stop()  # Stop clicked mid-publish
        assert result.honoured is False            # past the point of no return
        return remote.create_pr(token, metadata)   # remote publishes anyway

    session = make_session(
        remote, session_id="s-pub", billing=billing, create_fn=create_with_stop
    )
    holder["session"] = session
    outcome = session.run(b"patch")
    assert outcome is FinalOutcome.PUBLISHED
    assert len(remote.pr_creations) == 1
    charges = billing.all_charges()
    assert len(charges) == 1                      # exactly one charge
    assert session.final_outcome is FinalOutcome.PUBLISHED
    text = render_status(session)
    assert "published" in text.lower()
    assert remote.pr_creations[0] in text         # truthful: shows the PR URL
    assert "charged once" in text.lower()
    assert not text.lower().startswith("stopped")


def test_stop_after_publication_is_too_late_single_charge():
    remote = FakeRemote()
    billing = BillingLedger()
    session = make_session(remote, session_id="s-late", billing=billing)
    session.run(b"patch")
    assert session.final_outcome is FinalOutcome.PUBLISHED
    first = session.request_stop()
    assert first.honoured is False
    assert first.already_terminal is True
    assert "published" in first.reason
    second = session.request_stop()
    assert second.honoured is False
    assert len(billing.all_charges()) == 1        # still exactly one charge
    assert session.final_outcome is FinalOutcome.PUBLISHED
    text = render_status(session)
    assert "published" in text.lower()
    assert session.pr_url in text


def test_ui_during_publication_reports_point_of_no_return():
    remote = FakeRemote()
    release = threading.Event()

    def slow_create(token: str, metadata: dict) -> str:
        release.wait(timeout=5)
        return remote.create_pr(token, metadata)

    session = make_session(remote, session_id="s-live", create_fn=slow_create)
    session.upload_patch(b"patch")
    errors = []

    def worker() -> None:
        try:
            session.publish()
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    assert wait_for_phase(session, Phase.PUBLISHING), "publish never reached PUBLISHING"
    assert session.status().point_of_no_return_passed is True
    text = render_status(session)
    assert "point of no return" in text.lower()
    result = session.request_stop()
    assert result.honoured is False
    release.set()
    thread.join(timeout=5)
    assert errors == []
    assert session.final_outcome is FinalOutcome.PUBLISHED
    assert len(session.billing.all_charges()) == 1


# --------------------------------------------------------------------------
# Repeated stops and single final outcome
# --------------------------------------------------------------------------


def test_repeated_stop_requests_are_idempotent():
    remote = FakeRemote()
    session = make_session(remote)
    results = [session.request_stop() for _ in range(3)]
    assert all(r.honoured for r in results)
    assert session.stop_requests == 3
    outcome = session.run(b"patch")
    assert outcome is FinalOutcome.STOPPED
    assert session.stop_requests == 3
    assert session.has_charge() is False
    assert session.final_outcome is FinalOutcome.STOPPED


def test_final_outcome_cannot_change_after_terminal():
    remote = FakeRemote()
    session = make_session(remote)
    session.request_stop()
    assert session.run(b"patch") is FinalOutcome.STOPPED
    try:
        session.upload_patch(b"x")
        raised = False
    except InvalidTransitionError:
        raised = True
    assert raised is True
    try:
        session.publish()
        raised = False
    except InvalidTransitionError:
        raised = True
    assert raised is True
    assert session.run(b"x") is FinalOutcome.STOPPED  # run() always returns the outcome
    assert session.final_outcome is FinalOutcome.STOPPED


def test_published_session_cannot_publish_again_or_double_charge():
    remote = FakeRemote()
    billing = BillingLedger()
    session = make_session(remote, session_id="s-dbl", billing=billing)
    session.run(b"patch")
    try:
        session.publish()
        raised = False
    except InvalidTransitionError:
        raised = True
    assert raised is True
    assert len(billing.all_charges()) == 1
    assert len(remote.pr_creations) == 1


# --------------------------------------------------------------------------
# No duplicate charge
# --------------------------------------------------------------------------


def test_billing_ledger_never_duplicates_a_charge():
    ledger = BillingLedger()
    first = ledger.create_charge("s", 500, "first")
    second = ledger.create_charge("s", 500, "second attempt")
    assert first is second
    assert len(ledger.all_charges()) == 1
    try:
        ledger.create_charge("s", 999, "different amount")
        raised = False
    except DuplicateChargeError:
        raised = True
    assert raised is True
    assert len(ledger.all_charges()) == 1


# --------------------------------------------------------------------------
# Failure paths (clear outcomes, never charged)
# --------------------------------------------------------------------------


def test_publish_failure_without_stop_reports_failed_no_charge():
    remote = FakeRemote()
    remote.create_error = RuntimeError("remote refused")
    session = make_session(remote)
    outcome = session.run(b"patch")
    assert outcome is FinalOutcome.FAILED
    assert remote.pr_creations == []
    assert session.has_charge() is False
    assert render_status(session).startswith("Failed")


def test_publish_failure_with_stop_reports_stopped_no_charge():
    remote = FakeRemote()
    holder = {}

    def create_with_stop_then_fail(token: str, metadata: dict) -> str:
        holder["session"].request_stop()
        raise RuntimeError("remote refused")

    session = make_session(remote, session_id="s-fail", create_fn=create_with_stop_then_fail)
    holder["session"] = session
    outcome = session.run(b"patch")
    assert outcome is FinalOutcome.STOPPED  # nothing published -> stop honoured
    assert session.has_charge() is False


def test_upload_failure_reports_failed_no_charge():
    remote = FakeRemote()
    remote.upload_error = RuntimeError("disk full")
    session = make_session(remote)
    outcome = session.run(b"patch")
    assert outcome is FinalOutcome.FAILED
    assert session.has_charge() is False


# --------------------------------------------------------------------------
# Concurrency: Stop racing the publication
# --------------------------------------------------------------------------


def test_concurrent_stop_requests_yield_single_outcome_and_at_most_one_charge():
    remote = FakeRemote()
    billing = BillingLedger()
    session = make_session(remote, session_id="s-race", billing=billing)
    session.upload_patch(b"patch")
    barrier = threading.Barrier(8)

    def stopper() -> None:
        barrier.wait()
        session.request_stop()

    threads = [threading.Thread(target=stopper) for _ in range(8)]
    for thread in threads:
        thread.start()
    try:
        session.publish()
    except PublicationStopped:
        pass  # a stop landed before the point of no return
    for thread in threads:
        thread.join()
    assert session.is_terminal
    charges = billing.all_charges()
    if session.final_outcome is FinalOutcome.PUBLISHED:
        assert len(remote.pr_creations) == 1
        assert len(charges) == 1
        assert "published" in render_status(session).lower()
    else:
        assert session.final_outcome is FinalOutcome.STOPPED
        assert remote.pr_creations == []
        assert charges == []
        assert render_status(session).startswith("Stopped")


# --------------------------------------------------------------------------
# Standalone runner (no pytest required)
# --------------------------------------------------------------------------


def _main() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            print(f"PASS {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_main())
