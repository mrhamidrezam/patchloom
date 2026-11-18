"""patchloom — RFC 6902 JSON Patch, RFC 7396 Merge Patch, reversible diff & 3-way merge."""

from __future__ import annotations

from patchloom._core import (
    JsonArray,
    JsonObject,
    JsonScalar,
    JsonValue,
    clone_json,
    json_equal,
    normalize_json,
)
from patchloom.diff import diff, diff_reversible
from patchloom.errors import (
    MergeConflictError,
    PatchLoomError,
    PatchOperationError,
    PatchTestFailedError,
    PointerError,
)
from patchloom.merge3 import (
    MISSING,
    BranchValue,
    ConflictKind,
    ConflictPolicy,
    ConflictResolver,
    MergeConflict,
    MergeResult,
    merge3,
)
from patchloom.merge_patch import apply_merge_patch, create_merge_patch
from patchloom.patch import (
    OpKind,
    Patch,
    PatchOp,
    ReversiblePatch,
    apply_patch,
    invert_patch,
)
from patchloom.pointer import (
    Pointer,
    escape_token,
    resolve_pointer,
    unescape_token,
)

__version__ = "0.1.0"

__all__ = [
    "MISSING",
    "BranchValue",
    "ConflictKind",
    "ConflictPolicy",
    "ConflictResolver",
    "JsonArray",
    "JsonObject",
    "JsonScalar",
    "JsonValue",
    "MergeConflict",
    "MergeConflictError",
    "MergeResult",
    "OpKind",
    "Patch",
    "PatchLoomError",
    "PatchOp",
    "PatchOperationError",
    "PatchTestFailedError",
    "Pointer",
    "PointerError",
    "ReversiblePatch",
    "__version__",
    "apply_merge_patch",
    "apply_patch",
    "clone_json",
    "create_merge_patch",
    "diff",
    "diff_reversible",
    "escape_token",
    "invert_patch",
    "json_equal",
    "merge3",
    "normalize_json",
    "resolve_pointer",
    "unescape_token",
]
