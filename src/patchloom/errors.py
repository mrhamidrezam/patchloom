"""Typed exception hierarchy for patchloom."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchloom.merge3 import MergeConflict


class PatchLoomError(Exception):
    """Base exception for all patchloom errors."""


class PointerError(PatchLoomError):
    """Raised when an RFC 6901 JSON Pointer is malformed or cannot be traversed."""


class PatchOperationError(PatchLoomError):
    """Raised when an RFC 6902 JSON Patch operation is malformed or invalid against a target."""


class PatchTestFailedError(PatchOperationError):
    """Raised when an RFC 6902 ``test`` operation fails its value assertion."""


class MergeConflictError(PatchLoomError):
    """Raised during a three-way merge when unresolved conflicts exist under ``ConflictPolicy.RAISE``."""

    conflicts: tuple[MergeConflict, ...]

    def __init__(self, conflicts: tuple[MergeConflict, ...]) -> None:
        self.conflicts = conflicts
        paths = ", ".join(str(c.path) or "/" for c in conflicts)
        super().__init__(
            f"Three-way merge encountered {len(conflicts)} conflict(s) at: {paths}"
        )
