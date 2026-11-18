"""RFC 7396 JSON Merge Patch generation and application."""

from __future__ import annotations

from typing import Any

from patchloom._core import (
    JsonObject,
    JsonValue,
    clone_json,
    json_equal,
    normalize_json,
)


def apply_merge_patch(
    target: JsonValue,
    patch: JsonValue,
    *,
    in_place: bool = False,
) -> JsonValue:
    """Apply an RFC 7396 JSON Merge Patch to ``target``.

    Implements the exact algorithm defined in RFC 7396 §2:
      - Non-object patches replace ``target`` wholesale.
      - Object patches recursively merge into object targets (coercing non-object
        targets to ``{}``), deleting keys whose patch value is ``None`` (JSON ``null``).
    """
    if not isinstance(patch, dict):
        return clone_json(patch)

    if isinstance(target, dict):
        working: JsonObject = target if in_place else clone_json(target)
    else:
        working = {}

    for key, patch_val in patch.items():
        if patch_val is None:
            working.pop(key, None)
        else:
            existing = working.get(key)
            if isinstance(existing, dict) and isinstance(patch_val, dict):
                working[key] = apply_merge_patch(existing, patch_val, in_place=in_place)
            else:
                working[key] = apply_merge_patch(None, patch_val, in_place=False)

    return working


def create_merge_patch(
    source: Any,
    target: Any,
    *,
    normalize: bool = False,
) -> JsonValue:
    """Generate a minimal RFC 7396 JSON Merge Patch transforming ``source`` into ``target``.

    Note:
        Per RFC 7396 §2, JSON ``null`` inside an object merge patch always signifies
        member deletion. Therefore, explicit ``None`` values inside ``target`` objects
        cannot be represented as ``null`` additions in RFC 7396 (use RFC 6902 ``diff()``
        when explicit ``null`` object member values must be preserved). Calling this
        function when ``target`` contains an object member whose value is ``None`` raises
        ``ValueError`` unless that key is absent in ``target``.
    """
    src_val: JsonValue = normalize_json(source) if normalize else source
    dst_val: JsonValue = normalize_json(target) if normalize else target
    return _build_merge_patch(src_val, dst_val)


def _build_merge_patch(src: JsonValue, dst: JsonValue) -> JsonValue:
    if not isinstance(src, dict) or not isinstance(dst, dict):
        if isinstance(dst, dict):
            _check_no_explicit_null_members(dst)
        return clone_json(dst)

    patch: JsonObject = {}

    for key in sorted(src.keys() - dst.keys()):
        patch[key] = None

    for key in sorted(dst.keys()):
        dst_item = dst[key]
        if dst_item is None:
            raise ValueError(
                f"RFC 7396 Merge Patch cannot set object key {key!r} to explicit null "
                "(null denotes deletion in RFC 7396); use patchloom.diff() (RFC 6902) instead."
            )
        if key not in src:
            if isinstance(dst_item, dict):
                _check_no_explicit_null_members(dst_item)
            patch[key] = clone_json(dst_item)
        else:
            src_item = src[key]
            if not json_equal(src_item, dst_item):
                patch[key] = _build_merge_patch(src_item, dst_item)

    return patch


def _check_no_explicit_null_members(obj: JsonObject) -> None:
    for key, val in obj.items():
        if val is None:
            raise ValueError(
                f"RFC 7396 Merge Patch cannot represent explicit null at object key {key!r}; "
                "use patchloom.diff() (RFC 6902) instead."
            )
        if isinstance(val, dict):
            _check_no_explicit_null_members(val)
