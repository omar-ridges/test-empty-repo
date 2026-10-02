"""Idempotent billing for publication sessions (LIFE-03).

Billing rule: a session is charged at most once, and only when publication
actually happens. The ledger enforces this even under concurrent or repeated
attempts, so a Stop can never produce a duplicate charge and a published
session is charged exactly once.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

__all__ = ["BillingLedger", "Charge", "DuplicateChargeError"]


@dataclass(frozen=True)
class Charge:
    """A single charge for a publication session."""

    session_id: str
    amount_cents: int
    description: str


class DuplicateChargeError(RuntimeError):
    """Raised when a second, differing charge is attempted for a session."""


class BillingLedger:
    """Thread-safe ledger that guarantees at most one charge per session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._charges: Dict[str, Charge] = {}

    def create_charge(self, session_id: str, amount_cents: int, description: str = "") -> Charge:
        """Create the single charge for ``session_id``.

        Idempotent: if a charge already exists for the session it is returned
        unchanged and no second charge is created. Attempting to charge a
        different amount for the same session raises
        :class:`DuplicateChargeError`.
        """
        with self._lock:
            existing = self._charges.get(session_id)
            if existing is not None:
                if existing.amount_cents != amount_cents:
                    raise DuplicateChargeError(
                        f"session {session_id!r} was already charged "
                        f"{existing.amount_cents} cents; refusing duplicate "
                        f"charge of {amount_cents} cents"
                    )
                return existing
            charge = Charge(session_id=session_id, amount_cents=amount_cents, description=description)
            self._charges[session_id] = charge
            return charge

    def has_charge(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._charges

    def get_charge(self, session_id: str) -> Optional[Charge]:
        with self._lock:
            return self._charges.get(session_id)

    def all_charges(self) -> List[Charge]:
        with self._lock:
            return list(self._charges.values())
