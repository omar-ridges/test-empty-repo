"""Publication lifecycle with Stop support (LIFE-03).

Public API:
    PublicationSession   -- Stop-aware state machine for the assisted flow.
    BillingLedger/Charge -- idempotent billing (no duplicate charges).
    Phase/FinalOutcome   -- lifecycle phases and the single final outcome.
    render_status        -- truthful UI status rendering.
"""

from .billing import BillingLedger, Charge, DuplicateChargeError
from .session import (
    InvalidTransitionError,
    PublicationError,
    PublicationSession,
    PublicationStopped,
    SessionEvent,
    SessionStatus,
    StopResult,
)
from .states import TERMINAL_PHASES, FinalOutcome, Phase
from .ui import final_outcome_label, render_status

__all__ = [
    "BillingLedger",
    "Charge",
    "DuplicateChargeError",
    "FinalOutcome",
    "InvalidTransitionError",
    "Phase",
    "PublicationError",
    "PublicationSession",
    "PublicationStopped",
    "SessionEvent",
    "SessionStatus",
    "StopResult",
    "TERMINAL_PHASES",
    "final_outcome_label",
    "render_status",
]
