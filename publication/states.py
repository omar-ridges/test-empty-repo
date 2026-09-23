"""Phases and outcomes for the publication lifecycle (LIFE-03)."""

from __future__ import annotations

from enum import Enum

__all__ = ["Phase", "FinalOutcome", "TERMINAL_PHASES"]


class Phase(str, Enum):
    """Lifecycle phases of a publication session.

    Normal progression::

        IDLE -> UPLOADING_PATCH -> UPLOAD_COMPLETE -> PUBLISHING -> PUBLISHED

    ``PUBLISHED``, ``STOPPED`` and ``FAILED`` are terminal: exactly one of
    them becomes the session's single final outcome, and no further
    transitions are possible afterwards.
    """

    IDLE = "idle"
    UPLOADING_PATCH = "uploading_patch"
    UPLOAD_COMPLETE = "upload_complete"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    STOPPED = "stopped"
    FAILED = "failed"


TERMINAL_PHASES = frozenset({Phase.PUBLISHED, Phase.STOPPED, Phase.FAILED})


class FinalOutcome(str, Enum):
    """The single, clear final outcome of a publication session."""

    PUBLISHED = "published"
    STOPPED = "stopped"
    FAILED = "failed"
