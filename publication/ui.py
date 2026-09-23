"""Truthful UI rendering for publication sessions (LIFE-03).

The UI must show exactly one clear final outcome and must never claim
"stopped" for a PR that was actually published.
"""

from __future__ import annotations

from .session import PublicationSession
from .states import Phase

__all__ = ["render_status", "final_outcome_label"]


_PHASE_LABELS = {
    Phase.IDLE: "preparing",
    Phase.UPLOADING_PATCH: "the patch upload",
    Phase.UPLOAD_COMPLETE: "the pause after upload",
    Phase.PUBLISHING: "the PR publication",
}

_ACTIVE_HEADLINES = {
    Phase.IDLE: "Preparing publication",
    Phase.UPLOADING_PATCH: "Uploading patch",
    Phase.UPLOAD_COMPLETE: "Patch uploaded",
    Phase.PUBLISHING: "Publishing PR",
}


def _format_amount(amount_cents: int) -> str:
    return f"${amount_cents / 100:.2f}"


def final_outcome_label(session: PublicationSession) -> str:
    """The single final-outcome word for the UI, or the live phase."""
    status = session.status()
    if status.final_outcome is not None:
        return status.final_outcome.value
    return status.phase.value


def render_status(session: PublicationSession) -> str:
    """Render one truthful status message for the session."""
    status = session.status()

    if status.phase is Phase.PUBLISHED:
        message = f"Published: your PR was published at {status.pr_url}."
        if status.stop_requested:
            message += (
                " Stop was requested after the point of no return, so "
                "publication could not be prevented."
            )
        if status.charged:
            charge = session.charge()
            if charge is not None:
                message += f" You were charged once ({_format_amount(charge.amount_cents)})."
        return message

    if status.phase is Phase.STOPPED:
        message = "Stopped: no PR was published and you were not charged."
        if status.stop_requested_in_phase is not None:
            label = _PHASE_LABELS.get(
                status.stop_requested_in_phase, status.stop_requested_in_phase.value
            )
            message += f" (Stop was requested during {label}.)"
        return message

    if status.phase is Phase.FAILED:
        return (
            "Failed: publication did not complete. No PR was published "
            "and you were not charged."
        )

    headline = _ACTIVE_HEADLINES.get(status.phase, status.phase.value)
    if status.point_of_no_return_passed:
        return (
            f"{headline}... Stop can no longer prevent publication "
            "(the point of no return has passed)."
        )
    return (
        f"{headline}... You can still stop: no PR will be published "
        "and no charge will be made."
    )
