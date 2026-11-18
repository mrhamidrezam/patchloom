"""Three-way structural merge engine with fine-grained conflict detection and resolution."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from patchloom._core import (
    JsonObject,
    JsonValue,
    canonical_key,
    clone_json,
    json_equal,
    normalize_json,
)
from patchloom.errors import MergeConflictError
from patchloom.pointer import Pointer


class _MissingSentinel:
    """Sentinel representing an absent object key or array element in one branch."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<MISSING>"


MISSING: Final = _MissingSentinel()
type BranchValue = JsonValue | _MissingSentinel


class ConflictKind(StrEnum):
    """Classification of a three-way structural merge conflict."""

    MODIFY_MODIFY = "modify_modify"
    ADD_ADD = "add_add"
    DELETE_MODIFY = "delete_modify"
    MODIFY_DELETE = "modify_delete"


class ConflictPolicy(StrEnum):
    """Built-in automatic conflict resolution policies."""

    RAISE = "raise"
    OURS = "ours"
    THEIRS = "theirs"
    BASE = "base"


@dataclass(frozen=True, slots=True)
class MergeConflict:
    """Detailed record of a concurrent conflict at a specific RFC 6901 ``Pointer``."""

    path: Pointer
    kind: ConflictKind
    base: BranchValue
    ours: BranchValue
    theirs: BranchValue

    def to_dict(self) -> dict[str, Any]:
        """Serialize conflict metadata into a JSON-friendly dictionary."""
        return {
            "path": str(self.path),
            "kind": str(self.kind),
            "base": None if self.base is MISSING else clone_json(self.base),
            "ours": None if self.ours is MISSING else clone_json(self.ours),
            "theirs": None if self.theirs is MISSING else clone_json(self.theirs),
            "base_present": self.base is not MISSING,
            "ours_present": self.ours is not MISSING,
            "theirs_present": self.theirs is not MISSING,
        }


type ConflictResolver = Callable[[MergeConflict], BranchValue]


@dataclass(frozen=True, slots=True)
class MergeResult:
    """Outcome of a three-way structural merge."""

    value: JsonValue
    conflicts: tuple[MergeConflict, ...] = ()

    @property
    def is_clean(self) -> bool:
        """Return ``True`` if no conflicts occurred during the merge."""
        return len(self.conflicts) == 0


def merge3(
    base: Any,
    ours: Any,
    theirs: Any,
    *,
    policy: ConflictPolicy | ConflictResolver = ConflictPolicy.RAISE,
    array_keys: Mapping[str, str] | str | None = None,
    normalize: bool = False,
) -> MergeResult:
    """Perform a three-way structural merge of ``ours`` and ``theirs`` relative to ``base``.

    Args:
        base: Common ancestor document.
        ours: Local branch document.
        theirs: Remote / incoming branch document.
        policy: Either a ``ConflictPolicy`` (``RAISE``, ``OURS``, ``THEIRS``, ``BASE``)
            or a custom resolver callable ``(conflict: MergeConflict) -> JsonValue | MISSING``.
        array_keys: Optional identity key name (e.g. ``"id"``) or mapping of
            ``{pointer_pattern: key_name}`` enabling record-level 3-way array merges.
        normalize: Normalize dataclasses, tuples, mappings, and enums before merging.
    """
    b_val: JsonValue = normalize_json(base) if normalize else base
    o_val: JsonValue = normalize_json(ours) if normalize else ours
    t_val: JsonValue = normalize_json(theirs) if normalize else theirs

    engine = _Merge3Engine(policy=policy, array_keys=array_keys)
    merged = engine.merge_root(b_val, o_val, t_val)

    if policy == ConflictPolicy.RAISE and engine.conflicts:
        raise MergeConflictError(tuple(engine.conflicts))

    return MergeResult(value=merged, conflicts=tuple(engine.conflicts))


class _Merge3Engine:
    __slots__ = ("array_keys", "conflicts", "policy")

    def __init__(
        self,
        *,
        policy: ConflictPolicy | ConflictResolver,
        array_keys: Mapping[str, str] | str | None,
    ) -> None:
        self.policy = policy
        self.array_keys = array_keys
        self.conflicts: list[MergeConflict] = []

    def merge_root(
        self, base: JsonValue, ours: JsonValue, theirs: JsonValue
    ) -> JsonValue:
        res = self._merge_node(base, ours, theirs, Pointer.root())
        if res is MISSING:
            return None
        return res

    def _resolve_array_key(self, path: Pointer) -> str | None:
        if self.array_keys is None:
            return None
        if isinstance(self.array_keys, str):
            return self.array_keys
        for pattern, key_name in self.array_keys.items():
            if path.matches(pattern):
                return key_name
        return None

    def _record_and_resolve(self, conflict: MergeConflict) -> BranchValue:
        self.conflicts.append(conflict)
        if callable(self.policy):
            resolved = self.policy(conflict)
            return MISSING if resolved is MISSING else clone_json(resolved)
        if self.policy is ConflictPolicy.OURS or self.policy is ConflictPolicy.RAISE:
            return MISSING if conflict.ours is MISSING else clone_json(conflict.ours)
        if self.policy is ConflictPolicy.THEIRS:
            return MISSING if conflict.theirs is MISSING else clone_json(conflict.theirs)
        if self.policy is ConflictPolicy.BASE:
            return MISSING if conflict.base is MISSING else clone_json(conflict.base)
        raise ValueError(f"Unsupported conflict policy: {self.policy!r}")

    def _merge_node(
        self,
        base: JsonValue,
        ours: JsonValue,
        theirs: JsonValue,
        path: Pointer,
    ) -> BranchValue:
        if json_equal(ours, theirs):
            return clone_json(ours)
        if json_equal(base, ours):
            return clone_json(theirs)
        if json_equal(base, theirs):
            return clone_json(ours)

        if isinstance(base, dict) and isinstance(ours, dict) and isinstance(theirs, dict):
            return self._merge_objects(base, ours, theirs, path)

        if isinstance(base, list) and isinstance(ours, list) and isinstance(theirs, list):
            id_key = self._resolve_array_key(path)
            if id_key is not None and _all_keyed_dicts(base, ours, theirs, id_key):
                return self._merge_keyed_arrays(base, ours, theirs, path, id_key)

        conflict = MergeConflict(
            path=path,
            kind=ConflictKind.MODIFY_MODIFY,
            base=clone_json(base),
            ours=clone_json(ours),
            theirs=clone_json(theirs),
        )
        return self._record_and_resolve(conflict)

    def _merge_objects(
        self,
        base: JsonObject,
        ours: JsonObject,
        theirs: JsonObject,
        path: Pointer,
    ) -> JsonObject:
        merged: JsonObject = {}
        all_keys = sorted(base.keys() | ours.keys() | theirs.keys())

        for key in all_keys:
            child_path = path / key
            in_b = key in base
            in_o = key in ours
            in_t = key in theirs

            if in_b and in_o and in_t:
                val = self._merge_node(base[key], ours[key], theirs[key], child_path)
                if val is not MISSING:
                    merged[key] = val
                continue

            if in_b and not in_o and not in_t:
                # Both branches deleted the key
                continue

            if in_b and not in_o and in_t:
                if json_equal(base[key], theirs[key]):
                    # Deleted in ours, untouched in theirs -> delete wins cleanly
                    continue
                val = self._record_and_resolve(
                    MergeConflict(
                        path=child_path,
                        kind=ConflictKind.DELETE_MODIFY,
                        base=clone_json(base[key]),
                        ours=MISSING,
                        theirs=clone_json(theirs[key]),
                    )
                )
                if val is not MISSING:
                    merged[key] = val
                continue

            if in_b and in_o and not in_t:
                if json_equal(base[key], ours[key]):
                    # Deleted in theirs, untouched in ours -> delete wins cleanly
                    continue
                val = self._record_and_resolve(
                    MergeConflict(
                        path=child_path,
                        kind=ConflictKind.MODIFY_DELETE,
                        base=clone_json(base[key]),
                        ours=clone_json(ours[key]),
                        theirs=MISSING,
                    )
                )
                if val is not MISSING:
                    merged[key] = val
                continue

            if not in_b and in_o and not in_t:
                merged[key] = clone_json(ours[key])
                continue

            if not in_b and not in_o and in_t:
                merged[key] = clone_json(theirs[key])
                continue

            # Added in both ours and theirs (!in_b and in_o and in_t)
            o_item = ours[key]
            t_item = theirs[key]
            if json_equal(o_item, t_item):
                merged[key] = clone_json(o_item)
            elif isinstance(o_item, dict) and isinstance(t_item, dict):
                merged[key] = self._merge_objects({}, o_item, t_item, child_path)
            else:
                val = self._record_and_resolve(
                    MergeConflict(
                        path=child_path,
                        kind=ConflictKind.ADD_ADD,
                        base=MISSING,
                        ours=clone_json(o_item),
                        theirs=clone_json(t_item),
                    )
                )
                if val is not MISSING:
                    merged[key] = val

        return merged

    def _merge_keyed_arrays(
        self,
        base: list[JsonValue],
        ours: list[JsonValue],
        theirs: list[JsonValue],
        path: Pointer,
        id_key: str,
    ) -> list[JsonValue]:
        b_map = _index_by_key(base, id_key)
        o_map = _index_by_key(ours, id_key)
        t_map = _index_by_key(theirs, id_key)

        ordered_ids: list[Hashable] = []
        seen: set[Hashable] = set()
        for source_map in (o_map, t_map, b_map):
            for cid in source_map:
                if cid not in seen:
                    seen.add(cid)
                    ordered_ids.append(cid)

        result: list[JsonValue] = []
        for cid in ordered_ids:
            in_b = cid in b_map
            in_o = cid in o_map
            in_t = cid in t_map

            raw_id = (
                o_map[cid][id_key]
                if in_o
                else (t_map[cid][id_key] if in_t else b_map[cid][id_key])
            )
            elem_path = path / f"{id_key}={raw_id}"

            if in_b and in_o and in_t:
                val = self._merge_node(b_map[cid], o_map[cid], t_map[cid], elem_path)
                if val is not MISSING:
                    result.append(val)
            elif in_b and not in_o and not in_t:
                continue
            elif in_b and not in_o and in_t:
                if json_equal(b_map[cid], t_map[cid]):
                    continue
                val = self._record_and_resolve(
                    MergeConflict(
                        path=elem_path,
                        kind=ConflictKind.DELETE_MODIFY,
                        base=clone_json(b_map[cid]),
                        ours=MISSING,
                        theirs=clone_json(t_map[cid]),
                    )
                )
                if val is not MISSING:
                    result.append(val)
            elif in_b and in_o and not in_t:
                if json_equal(b_map[cid], o_map[cid]):
                    continue
                val = self._record_and_resolve(
                    MergeConflict(
                        path=elem_path,
                        kind=ConflictKind.MODIFY_DELETE,
                        base=clone_json(b_map[cid]),
                        ours=clone_json(o_map[cid]),
                        theirs=MISSING,
                    )
                )
                if val is not MISSING:
                    result.append(val)
            elif not in_b and in_o and not in_t:
                result.append(clone_json(o_map[cid]))
            elif not in_b and not in_o and in_t:
                result.append(clone_json(t_map[cid]))
            else:
                # Added in both ours and theirs with the same identity key
                if json_equal(o_map[cid], t_map[cid]):
                    result.append(clone_json(o_map[cid]))
                else:
                    result.append(
                        self._merge_objects({}, o_map[cid], t_map[cid], elem_path)
                    )

        return result


def _all_keyed_dicts(
    base: list[JsonValue],
    ours: list[JsonValue],
    theirs: list[JsonValue],
    id_key: str,
) -> bool:
    for seq in (base, ours, theirs):
        seen: set[Hashable] = set()
        for item in seq:
            if not isinstance(item, dict) or id_key not in item:
                return False
            cid = canonical_key(item[id_key])
            if cid in seen:
                return False
            seen.add(cid)
    return True


def _index_by_key(seq: list[JsonValue], id_key: str) -> dict[Hashable, JsonObject]:
    out: dict[Hashable, JsonObject] = {}
    for item in seq:
        assert isinstance(item, dict)
        out[canonical_key(item[id_key])] = item
    return out
