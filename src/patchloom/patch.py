"""RFC 6902 JSON Patch engine with transactional atomicity and exact inverse patch generation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self

from patchloom._core import JsonObject, JsonValue, clone_json, json_equal
from patchloom.errors import PatchOperationError, PatchTestFailedError, PointerError
from patchloom.pointer import Pointer, parse_array_index

_UNSET: Any = object()


class OpKind(StrEnum):
    """Standard RFC 6902 §4 JSON Patch operation verbs."""

    ADD = "add"
    REMOVE = "remove"
    REPLACE = "replace"
    MOVE = "move"
    COPY = "copy"
    TEST = "test"


@dataclass(frozen=True, slots=True)
class PatchOp:
    """Single immutable RFC 6902 JSON Patch operation."""

    op: OpKind
    path: Pointer
    value: JsonValue = _UNSET
    from_path: Pointer | None = None

    def __post_init__(self) -> None:
        if self.op in (OpKind.ADD, OpKind.REPLACE, OpKind.TEST):
            if self.value is _UNSET:
                raise PatchOperationError(
                    f"Operation '{self.op}' at '{self.path}' requires a 'value' member."
                )
        elif self.op in (OpKind.MOVE, OpKind.COPY):
            if self.from_path is None:
                raise PatchOperationError(
                    f"Operation '{self.op}' at '{self.path}' requires a 'from' member."
                )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> Self:
        """Parse and validate a raw RFC 6902 operation mapping."""
        if not isinstance(raw, Mapping):
            raise PatchOperationError(
                f"Patch operation must be a JSON object, got {type(raw).__name__}."
            )
        if "op" not in raw:
            raise PatchOperationError("Patch operation missing required 'op' field.")
        if "path" not in raw or not isinstance(raw["path"], str):
            raise PatchOperationError("Patch operation missing valid string 'path' field.")

        try:
            kind = OpKind(raw["op"])
        except ValueError as exc:
            raise PatchOperationError(f"Unknown RFC 6902 operation {raw['op']!r}.") from exc

        try:
            path = Pointer.parse(raw["path"])
        except PointerError as exc:
            raise PatchOperationError(f"Invalid 'path' pointer: {exc}") from exc

        if kind in (OpKind.ADD, OpKind.REPLACE, OpKind.TEST):
            if "value" not in raw:
                raise PatchOperationError(
                    f"Operation '{kind}' at '{path}' missing required 'value' field."
                )
            return cls(op=kind, path=path, value=clone_json(raw["value"]))

        if kind in (OpKind.MOVE, OpKind.COPY):
            if "from" not in raw or not isinstance(raw["from"], str):
                raise PatchOperationError(
                    f"Operation '{kind}' at '{path}' missing valid string 'from' field."
                )
            try:
                from_ptr = Pointer.parse(raw["from"])
            except PointerError as exc:
                raise PatchOperationError(f"Invalid 'from' pointer: {exc}") from exc
            return cls(op=kind, path=path, from_path=from_ptr)

        return cls(op=kind, path=path)

    def to_dict(self) -> JsonObject:
        """Serialize to a standard RFC 6902 JSON object."""
        payload: JsonObject = {"op": str(self.op), "path": str(self.path)}
        if self.op in (OpKind.ADD, OpKind.REPLACE, OpKind.TEST):
            payload["value"] = clone_json(self.value)
        elif self.op in (OpKind.MOVE, OpKind.COPY):
            assert self.from_path is not None
            payload["from"] = str(self.from_path)
        return payload


class Patch(Sequence[PatchOp]):
    """Immutable, composable sequence of RFC 6902 JSON Patch operations."""

    __slots__ = ("_ops",)

    def __init__(self, ops: Iterable[PatchOp | Mapping[str, Any]] = ()) -> None:
        parsed: list[PatchOp] = []
        for item in ops:
            if isinstance(item, PatchOp):
                parsed.append(item)
            else:
                parsed.append(PatchOp.from_dict(item))
        self._ops: tuple[PatchOp, ...] = tuple(parsed)

    @classmethod
    def from_json(cls, payload: str | bytes) -> Self:
        """Deserialize an RFC 6902 JSON Patch document from a JSON string or UTF-8 bytes."""
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise PatchOperationError(f"Invalid JSON Patch payload: {exc}") from exc
        if not isinstance(decoded, list):
            raise PatchOperationError(
                f"JSON Patch document must be a JSON array, got {type(decoded).__name__}."
            )
        return cls(decoded)

    def to_list(self) -> list[JsonObject]:
        """Convert operations into a list of RFC 6902 dictionaries."""
        return [op.to_dict() for op in self._ops]

    def to_json(self, *, indent: int | None = None) -> str:
        """Serialize this patch to a JSON string."""
        return json.dumps(self.to_list(), indent=indent, ensure_ascii=False)

    def apply(self, document: JsonValue, *, in_place: bool = False) -> JsonValue:
        """Apply this patch atomically to ``document`` per RFC 6902 §5.

        If any operation fails, ``document`` is left completely unmodified even when
        ``in_place=True``.
        """
        working = clone_json(document)
        for op in self._ops:
            working, _ = _execute_single_op(working, op, build_inverse=False, include_tests=False)

        if in_place and type(document) is type(working):
            if isinstance(document, dict):
                assert isinstance(working, dict)
                document.clear()
                document.update(working)
                return document
            if isinstance(document, list):
                assert isinstance(working, list)
                document[:] = working
                return document
        return working

    def invert_against(self, document: JsonValue, *, include_tests: bool = True) -> Patch:
        """Compute the exact inverse RFC 6902 ``Patch`` relative to ``document``.

        Applying ``self`` to ``document`` yields ``target``; applying the returned inverse
        patch to ``target`` restores ``document`` exactly.
        """
        working = clone_json(document)
        step_inverses: list[list[PatchOp]] = []
        for op in self._ops:
            working, inv_ops = _execute_single_op(
                working, op, build_inverse=True, include_tests=include_tests
            )
            step_inverses.append(inv_ops)

        reversed_ops: list[PatchOp] = []
        for inv_ops in reversed(step_inverses):
            reversed_ops.extend(inv_ops)
        return Patch(reversed_ops)

    def __bool__(self) -> bool:
        return len(self._ops) > 0

    def __len__(self) -> int:
        return len(self._ops)

    def __iter__(self) -> Iterator[PatchOp]:
        return iter(self._ops)

    def __getitem__(self, index: int | slice) -> Any:
        if isinstance(index, slice):
            return Patch(self._ops[index])
        return self._ops[index]

    def __add__(self, other: Patch | Iterable[PatchOp | Mapping[str, Any]]) -> Patch:
        other_patch = other if isinstance(other, Patch) else Patch(other)
        return Patch((*self._ops, *other_patch._ops))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Patch):
            return NotImplemented
        if len(self._ops) != len(other._ops):
            return False
        return all(
            json_equal(a.to_dict(), b.to_dict())
            for a, b in zip(self._ops, other._ops, strict=True)
        )

    def __repr__(self) -> str:
        return f"Patch({self.to_list()!r})"


@dataclass(frozen=True, slots=True)
class ReversiblePatch:
    """Paired forward and reverse RFC 6902 patches for deterministic state rollback / undo-redo."""

    forward: Patch
    reverse: Patch

    def apply(self, document: JsonValue, *, in_place: bool = False) -> JsonValue:
        """Apply the forward patch."""
        return self.forward.apply(document, in_place=in_place)

    def rollback(self, document: JsonValue, *, in_place: bool = False) -> JsonValue:
        """Apply the reverse patch to roll ``document`` back to its prior state."""
        return self.reverse.apply(document, in_place=in_place)

    def swap(self) -> ReversiblePatch:
        """Return a new ``ReversiblePatch`` with forward and reverse swapped."""
        return ReversiblePatch(forward=self.reverse, reverse=self.forward)

    def to_dict(self) -> dict[str, list[JsonObject]]:
        """Serialize both patch directions to a JSON-compatible dictionary."""
        return {
            "forward": self.forward.to_list(),
            "reverse": self.reverse.to_list(),
        }


def apply_patch(
    document: JsonValue,
    patch: Patch | Iterable[PatchOp | Mapping[str, Any]],
    *,
    in_place: bool = False,
) -> JsonValue:
    """Apply an RFC 6902 JSON Patch (``Patch`` or list of operation dicts) to ``document``."""
    compiled = patch if isinstance(patch, Patch) else Patch(patch)
    return compiled.apply(document, in_place=in_place)


def invert_patch(
    document: JsonValue,
    patch: Patch | Iterable[PatchOp | Mapping[str, Any]],
    *,
    include_tests: bool = True,
) -> Patch:
    """Build the inverse RFC 6902 ``Patch`` for ``patch`` evaluated against ``document``."""
    compiled = patch if isinstance(patch, Patch) else Patch(patch)
    return compiled.invert_against(document, include_tests=include_tests)


def _execute_single_op(
    state: JsonValue,
    op: PatchOp,
    *,
    build_inverse: bool,
    include_tests: bool,
) -> tuple[JsonValue, list[PatchOp]]:
    try:
        if op.op is OpKind.ADD:
            new_state, inv, _, _ = _op_add(
                state, op.path, op.value, build_inverse=build_inverse, include_tests=include_tests
            )
            return new_state, inv

        if op.op is OpKind.REMOVE:
            new_state, _, inv, _ = _op_remove(
                state, op.path, build_inverse=build_inverse
            )
            return new_state, inv

        if op.op is OpKind.REPLACE:
            return _op_replace(
                state, op.path, op.value, build_inverse=build_inverse, include_tests=include_tests
            )

        if op.op is OpKind.MOVE:
            assert op.from_path is not None
            return _op_move(
                state,
                op.from_path,
                op.path,
                build_inverse=build_inverse,
                include_tests=include_tests,
            )

        if op.op is OpKind.COPY:
            assert op.from_path is not None
            return _op_copy(
                state,
                op.from_path,
                op.path,
                build_inverse=build_inverse,
                include_tests=include_tests,
            )

        if op.op is OpKind.TEST:
            return _op_test(
                state, op.path, op.value, build_inverse=build_inverse, include_tests=include_tests
            )
    except PatchOperationError:
        raise
    except PointerError as exc:
        raise PatchOperationError(
            f"Operation '{op.op}' failed at '{op.path}': {exc}"
        ) from exc

    raise PatchOperationError(f"Unsupported operation '{op.op}'.")


def _op_add(
    state: JsonValue,
    path: Pointer,
    value: JsonValue,
    *,
    build_inverse: bool,
    include_tests: bool,
) -> tuple[JsonValue, list[PatchOp], Pointer, bool]:
    val_copy = clone_json(value)
    if path.is_root:
        inv: list[PatchOp] = []
        if build_inverse:
            if include_tests:
                inv.append(PatchOp(OpKind.TEST, Pointer.root(), clone_json(val_copy)))
            inv.append(PatchOp(OpKind.REPLACE, Pointer.root(), clone_json(state)))
        return val_copy, inv, Pointer.root(), True

    container, leaf = path.resolve_parent(state)
    if isinstance(container, dict):
        overwrote = leaf in container
        old_val = clone_json(container[leaf]) if overwrote and build_inverse else None
        container[leaf] = val_copy
        inv = []
        if build_inverse:
            if include_tests:
                inv.append(PatchOp(OpKind.TEST, path, clone_json(val_copy)))
            if overwrote:
                inv.append(PatchOp(OpKind.REPLACE, path, old_val))
            else:
                inv.append(PatchOp(OpKind.REMOVE, path))
        return state, inv, path, overwrote

    idx = parse_array_index(leaf, len(container), allow_end=True)
    container.insert(idx, val_copy)
    concrete_path = path.parent / idx
    inv = []
    if build_inverse:
        if include_tests:
            inv.append(PatchOp(OpKind.TEST, concrete_path, clone_json(val_copy)))
        inv.append(PatchOp(OpKind.REMOVE, concrete_path))
    return state, inv, concrete_path, False


def _op_remove(
    state: JsonValue,
    path: Pointer,
    *,
    build_inverse: bool,
) -> tuple[JsonValue, JsonValue, list[PatchOp], Pointer]:
    if path.is_root:
        raise PatchOperationError("Cannot 'remove' the root document pointer ''.")

    container, leaf = path.resolve_parent(state)
    if isinstance(container, dict):
        if leaf not in container:
            raise PatchOperationError(f"Cannot remove non-existent key {leaf!r} at '{path}'.")
        removed = container.pop(leaf)
        inv = [PatchOp(OpKind.ADD, path, clone_json(removed))] if build_inverse else []
        return state, removed, inv, path

    idx = parse_array_index(leaf, len(container), allow_end=False)
    removed = container.pop(idx)
    concrete_from = path.parent / idx
    inv = [PatchOp(OpKind.ADD, concrete_from, clone_json(removed))] if build_inverse else []
    return state, removed, inv, concrete_from


def _op_replace(
    state: JsonValue,
    path: Pointer,
    value: JsonValue,
    *,
    build_inverse: bool,
    include_tests: bool,
) -> tuple[JsonValue, list[PatchOp]]:
    val_copy = clone_json(value)
    if path.is_root:
        inv: list[PatchOp] = []
        if build_inverse:
            if include_tests:
                inv.append(PatchOp(OpKind.TEST, Pointer.root(), clone_json(val_copy)))
            inv.append(PatchOp(OpKind.REPLACE, Pointer.root(), clone_json(state)))
        return val_copy, inv

    container, leaf = path.resolve_parent(state)
    if isinstance(container, dict):
        if leaf not in container:
            raise PatchOperationError(
                f"Cannot 'replace' non-existent member {leaf!r} at '{path}'."
            )
        old_val = container[leaf]
        container[leaf] = val_copy
    else:
        idx = parse_array_index(leaf, len(container), allow_end=False)
        old_val = container[idx]
        container[idx] = val_copy

    inv = []
    if build_inverse:
        if include_tests:
            inv.append(PatchOp(OpKind.TEST, path, clone_json(val_copy)))
        inv.append(PatchOp(OpKind.REPLACE, path, clone_json(old_val)))
    return state, inv


def _op_move(
    state: JsonValue,
    from_path: Pointer,
    path: Pointer,
    *,
    build_inverse: bool,
    include_tests: bool,
) -> tuple[JsonValue, list[PatchOp]]:
    if from_path.is_proper_prefix_of(path):
        raise PatchOperationError(
            f"Invalid 'move': 'from' pointer '{from_path}' cannot be a proper prefix of 'path' '{path}'."
        )

    if from_path == path:
        resolved = from_path.resolve(state)
        inv = (
            [PatchOp(OpKind.TEST, path, clone_json(resolved))]
            if (build_inverse and include_tests)
            else []
        )
        return state, inv

    state, moved_val, inv_rem, concrete_from = _op_remove(
        state, from_path, build_inverse=build_inverse
    )
    state, inv_add, concrete_path, overwrote = _op_add(
        state,
        path,
        moved_val,
        build_inverse=build_inverse,
        include_tests=include_tests,
    )

    if not build_inverse:
        return state, []

    if not overwrote and not concrete_path.is_root:
        collapsed: list[PatchOp] = []
        if include_tests:
            collapsed.append(PatchOp(OpKind.TEST, concrete_path, clone_json(moved_val)))
        collapsed.append(
            PatchOp(OpKind.MOVE, path=concrete_from, from_path=concrete_path)
        )
        return state, collapsed

    return state, [*inv_add, *inv_rem]


def _op_copy(
    state: JsonValue,
    from_path: Pointer,
    path: Pointer,
    *,
    build_inverse: bool,
    include_tests: bool,
) -> tuple[JsonValue, list[PatchOp]]:
    copied_val = clone_json(from_path.resolve(state))
    state, inv_add, _, _ = _op_add(
        state,
        path,
        copied_val,
        build_inverse=build_inverse,
        include_tests=include_tests,
    )
    return state, inv_add


def _op_test(
    state: JsonValue,
    path: Pointer,
    expected: JsonValue,
    *,
    build_inverse: bool,
    include_tests: bool,
) -> tuple[JsonValue, list[PatchOp]]:
    try:
        actual = path.resolve(state)
    except PointerError as exc:
        raise PatchTestFailedError(
            f"Test failed at '{path}': target location does not exist ({exc})."
        ) from exc

    if not json_equal(actual, expected):
        raise PatchTestFailedError(
            f"Test failed at '{path}': expected {expected!r}, got {actual!r}."
        )

    inv = (
        [PatchOp(OpKind.TEST, path, clone_json(expected))]
        if (build_inverse and include_tests)
        else []
    )
    return state, inv
