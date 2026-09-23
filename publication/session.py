"""Stop-aware publication session (LIFE-03).

Implements the publication state machine for the assisted flow: patch upload
followed by pull-request (PR) publication, with a Stop action that is
available at every checkpoint before the point of no return.

The agreed point of no return
-----------------------------
``PublicationSession.POINT_OF_NO_RETURN`` states the agreed rule: the point
of no return is the transition to the ``PUBLISHING`` phase -- the moment the
session commits to dispatching the create-PR request to the remote host.

* A Stop request recorded *before* that transition is always honoured: no PR
  is created and no charge is made.
* A Stop request recorded *at or after* that transition cannot prevent
  publication (the remote may complete the request regardless). The session
  then reports the outcome that actually occurred: ``PUBLISHED`` (with
  exactly one charge) if the remote accepted the request, ``STOPPED`` if it
  did not and nothing was published.

Guarantees
----------
* **One clear final outcome.** Exactly one terminal phase (``PUBLISHED``,
  ``STOPPED`` or ``FAILED``) is reached and can never change afterwards.
* **No duplicate charge.** The charge is created exactly once, atomically
  with the ``PUBLISHED`` transition; stopped and failed sessions are never
  charged. The idempotent :class:`~publication.billing.BillingLedger` is a
  second line of defence.
* **Truthful reporting.** ``status()``/``render_status()`` never claim
  "stopped" for a PR that was actually published.

Thread-safety
-------------
``request_stop()`` may be called from any thread (e.g. the UI Stop button)
while ``upload_patch()``/``publish()``/``run()`` execute on the worker side.
Long-running adapters can cooperatively cancel by polling
``session.stop_requested``.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .billing import BillingLedger, Charge
from .states import TERMINAL_PHASES, FinalOutcome, Phase

__all__ = [
    "PublicationSession",
    "PublicationStopped",
    "PublicationError",
    "InvalidTransitionError",
    "StopResult",
    "SessionEvent",
    "SessionStatus",
]


class PublicationStopped(Exception):
    """Raised at a checkpoint when a Stop request has been honoured."""


class PublicationError(Exception):
    """Raised when a publication step fails."""


class InvalidTransitionError(RuntimeError):
    """Raised when an action is attempted in the wrong phase."""


@dataclass(frozen=True)
class StopResult:
    """Disposition of a Stop request. Repeated requests are safe."""

    honoured: bool
    already_terminal: bool
    reason: str


@dataclass(frozen=True)
class SessionEvent:
    """An audit-log entry for a phase transition."""

    at: float
    phase: Phase
    detail: str


@dataclass(frozen=True)
class SessionStatus:
    """Immutable snapshot of a session's state."""

    session_id: str
    phase: Phase
    final_outcome: Optional[FinalOutcome]
    stop_requested: bool
    stop_requests: int
    stop_requested_in_phase: Optional[Phase]
    pr_url: Optional[str]
    charged: bool
    point_of_no_return_passed: bool


def _default_upload_patch(patch: bytes) -> str:
    """In-memory stand-in for the real patch upload (demos/tests only)."""
    return f"upload-{hashlib.sha256(patch).hexdigest()[:12]}"


def _default_create_pr(upload_token: str, metadata: Dict[str, object]) -> str:
    """In-memory stand-in for the real PR creation (demos/tests only)."""
    branch = str((metadata or {}).get("branch", "patch"))
    return f"https://pr.example/{branch}/{upload_token}"


class PublicationSession:
    """Stop-aware state machine for one publication (assisted flow)."""

    POINT_OF_NO_RETURN = (
        "The transition to the PUBLISHING phase: the moment the session "
        "commits to dispatching the create-PR request to the remote host. "
        "Stop requests recorded before this point are always honoured; "
        "requests recorded at or after it cannot prevent publication."
    )

    def __init__(
        self,
        session_id: str,
        *,
        upload_patch_fn: Optional[Callable[[bytes], str]] = None,
        create_pr_fn: Optional[Callable[[str, Dict[str, object]], str]] = None,
        billing: Optional[BillingLedger] = None,
        amount_cents: int = 0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.session_id = session_id
        self._clock = clock
        self._lock = threading.RLock()
        self._phase = Phase.IDLE
        self._final_outcome: Optional[FinalOutcome] = None
        self._stop_requested = False
        self._stop_requested_at: Optional[float] = None
        self._stop_requested_in_phase: Optional[Phase] = None
        self._stop_requests = 0
        self._point_of_no_return_passed = False
        self._pr_url: Optional[str] = None
        self._upload_token: Optional[str] = None
        self._billing = billing if billing is not None else BillingLedger()
        self._amount_cents = amount_cents
        self._upload_patch_fn = upload_patch_fn or _default_upload_patch
        self._create_pr_fn = create_pr_fn or _default_create_pr
        self._events: List[SessionEvent] = []

    # ------------------------------------------------------------------ state
    @property
    def phase(self) -> Phase:
        with self._lock:
            return self._phase

    @property
    def final_outcome(self) -> Optional[FinalOutcome]:
        with self._lock:
            return self._final_outcome

    @property
    def is_terminal(self) -> bool:
        with self._lock:
            return self._phase in TERMINAL_PHASES

    @property
    def stop_requested(self) -> bool:
        """True once a Stop has been requested (safe to poll from adapters)."""
        with self._lock:
            return self._stop_requested

    @property
    def stop_requests(self) -> int:
        with self._lock:
            return self._stop_requests

    @property
    def stop_requested_in_phase(self) -> Optional[Phase]:
        with self._lock:
            return self._stop_requested_in_phase

    @property
    def point_of_no_return_passed(self) -> bool:
        with self._lock:
            return self._point_of_no_return_passed

    @property
    def pr_url(self) -> Optional[str]:
        with self._lock:
            return self._pr_url

    @property
    def billing(self) -> BillingLedger:
        return self._billing

    @property
    def events(self) -> tuple:
        with self._lock:
            return tuple(self._events)

    def has_charge(self) -> bool:
        return self._billing.has_charge(self.session_id)

    def charge(self) -> Optional[Charge]:
        return self._billing.get_charge(self.session_id)

    def status(self) -> SessionStatus:
        with self._lock:
            return SessionStatus(
                session_id=self.session_id,
                phase=self._phase,
                final_outcome=self._final_outcome,
                stop_requested=self._stop_requested,
                stop_requests=self._stop_requests,
                stop_requested_in_phase=self._stop_requested_in_phase,
                pr_url=self._pr_url,
                charged=self._billing.has_charge(self.session_id),
                point_of_no_return_passed=self._point_of_no_return_passed,
            )

    # ------------------------------------------------------------- Stop action
    def request_stop(self) -> StopResult:
        """Request stopping the publication (idempotent, thread-safe).

        Safe to call repeatedly and from any thread. Before the point of no
        return the stop is honoured at the next checkpoint; at or after it,
        the result explains that publication can no longer be prevented and
        the final outcome will truthfully report what happened.
        """
        now = self._clock()
        with self._lock:
            self._stop_requests += 1
            if self._phase is Phase.PUBLISHED:
                return StopResult(
                    honoured=False,
                    already_terminal=True,
                    reason=(
                        f"The PR was already published at {self._pr_url}. "
                        "Stop cannot prevent publication after the point of "
                        "no return."
                    ),
                )
            if self._phase is Phase.FAILED:
                return StopResult(
                    honoured=False,
                    already_terminal=True,
                    reason=(
                        "The session already failed: nothing was published "
                        "and no charge was made."
                    ),
                )
            if self._phase is Phase.STOPPED:
                return StopResult(
                    honoured=True,
                    already_terminal=True,
                    reason="The session is already stopped; that is the final outcome.",
                )
            if not self._stop_requested:
                self._stop_requested = True
                self._stop_requested_at = now
                self._stop_requested_in_phase = self._phase
            if self._phase is Phase.PUBLISHING:
                return StopResult(
                    honoured=False,
                    already_terminal=False,
                    reason=(
                        "The publish step has already been committed (the point "
                        "of no return has passed): Stop can no longer prevent "
                        "publication. The final outcome will truthfully report "
                        "what happened."
                    ),
                )
            return StopResult(
                honoured=True,
                already_terminal=False,
                reason=(
                    "Stop accepted: it will be honoured at the next checkpoint, "
                    "before the point of no return. No PR will be published "
                    "and no charge will be made."
                ),
            )

    # -------------------------------------------------------- internal helpers
    def _record(self, phase: Phase, detail: str) -> None:
        """Append an audit event. The session lock must be held."""
        self._events.append(SessionEvent(at=self._clock(), phase=phase, detail=detail))

    def _require_phase(self, expected: Phase, action: str) -> None:
        if self._phase is not expected:
            raise InvalidTransitionError(
                f"cannot {action} in phase {self._phase.value!r}; "
                f"expected {expected.value!r}"
            )

    def _transition(self, new_phase: Phase, detail: str = "") -> None:
        """Move to ``new_phase``. The session lock must be held.

        Terminal states are final: once reached, the outcome can never change
        (this is what guarantees exactly one clear final outcome).
        """
        if self._phase in TERMINAL_PHASES:
            raise InvalidTransitionError(
                f"session is already terminal ({self._phase.value}); the "
                "final outcome can no longer change"
            )
        self._phase = new_phase
        self._record(new_phase, detail)
        if new_phase in TERMINAL_PHASES:
            self._final_outcome = FinalOutcome(new_phase.value)

    def _checkpoint(self, where: str) -> None:
        """Honour a pending Stop request: transition to STOPPED and raise.

        The session lock must be held.
        """
        if self._stop_requested and self._phase not in TERMINAL_PHASES:
            self._transition(Phase.STOPPED, detail=f"stop honoured at {where}")
            raise PublicationStopped(f"publication stopped at {where}")

    # ---------------------------------------------------- assisted flow steps
    def upload_patch(self, patch: bytes) -> str:
        """Upload the patch. Stop is honoured before and after the upload.

        Long uploads may cooperatively cancel by polling
        ``session.stop_requested`` and returning/raising early; either way,
        the post-upload checkpoint guarantees the stop takes effect before
        any publication is attempted.
        """
        with self._lock:
            self._require_phase(Phase.IDLE, "upload the patch")
            self._checkpoint("before patch upload")
            self._transition(Phase.UPLOADING_PATCH, detail="patch upload started")
        try:
            token = self._upload_patch_fn(patch)
        except Exception as exc:
            with self._lock:
                if self._stop_requested:
                    self._transition(Phase.STOPPED, detail="stop honoured during patch upload")
                    raise PublicationStopped("publication stopped during patch upload") from exc
                self._transition(Phase.FAILED, detail=f"patch upload failed: {exc}")
                raise PublicationError(f"patch upload failed: {exc}") from exc
        with self._lock:
            self._upload_token = token
            self._checkpoint("after patch upload")
            self._transition(Phase.UPLOAD_COMPLETE, detail="patch upload completed")
        return token

    def publish(self, pr_metadata: Optional[Dict[str, object]] = None) -> str:
        """Publish the PR. Stop is honoured up to the point of no return.

        The point of no return is the transition to ``PUBLISHING`` below: from
        that moment the create-PR request is committed for dispatch and Stop
        cannot prevent publication. If the remote does not publish anything,
        a pending Stop is still honoured (``STOPPED``, no charge); if the PR
        is created, the outcome is ``PUBLISHED`` with exactly one charge.
        """
        with self._lock:
            self._require_phase(Phase.UPLOAD_COMPLETE, "publish the PR")
            self._checkpoint("before PR publication")
            # ---- POINT OF NO RETURN (see POINT_OF_NO_RETURN) -------------
            self._transition(
                Phase.PUBLISHING,
                detail="publish committed; past the point of no return",
            )
            self._point_of_no_return_passed = True
        try:
            pr_url = self._create_pr_fn(self._upload_token, pr_metadata or {})
        except Exception as exc:
            with self._lock:
                if self._stop_requested:
                    # Nothing was published, so the user's stop is honoured.
                    self._transition(
                        Phase.STOPPED,
                        detail=(
                            "stop honoured: publish failed after the point of "
                            "no return (nothing published)"
                        ),
                    )
                    raise PublicationStopped(
                        "publication stopped; the PR was not published"
                    ) from exc
                self._transition(Phase.FAILED, detail=f"PR publication failed: {exc}")
                raise PublicationError(f"PR publication failed: {exc}") from exc
        with self._lock:
            self._pr_url = pr_url
            self._transition(Phase.PUBLISHED, detail=f"PR published at {pr_url}")
            # Exactly one charge, created only because publication happened.
            self._billing.create_charge(
                self.session_id,
                self._amount_cents,
                description=f"publication of {pr_url}",
            )
        return pr_url

    def run(self, patch: bytes, pr_metadata: Optional[Dict[str, object]] = None) -> FinalOutcome:
        """Run the full assisted flow (upload then publish).

        Always returns the session's single final outcome; step failures are
        recorded on the session (see ``events``) and reflected in the outcome
        instead of being raised.
        """
        try:
            self.upload_patch(patch)
            self.publish(pr_metadata)
        except (PublicationStopped, PublicationError, InvalidTransitionError):
            pass
        return self.final_outcome
