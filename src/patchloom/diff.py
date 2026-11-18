"""LCS-backed structural diff engine with RFC 6902 move/copy optimization and reversible patches."""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping
from typing import Any

from patchloom._core import (
    JsonObject,
    JsonValue,
    canonical_key,
    clone_json,
    json_equal,
    normalize_json,
)
from patchloom.patch import OpKind, Patch, PatchOp, ReversiblePatch
from patchloom.pointer import Pointer


def diff(
    source: Any,
    target: Any,
    *,
    detect_moves: bool = True,
    detect_copies: bool = False,
    ignore: Iterable[str | Pointer] = (),
    array_keys: Mapping[str, str] | str | None = None,
    float_epsilon: float = 0.0,
    normalize: bool = False,
    max_lcs_cells: int = 65_536,
) -> Patch:
    """Compute a deterministic RFC 6902 JSON ``Patch`` transforming ``source`` into ``target``.

    Args:
        source: Origin JSON value (or Python object when ``normalize=True``).
        target: Desired JSON value (or Python object when ``normalize=True``).
        detect_moves: Coalesce matching object field removals + additions into RFC 6902 ``move`` ops.
        detect_copies: Deduplicate added structural subtrees that already exist in the document via ``copy``.
        ignore: Exact JSON Pointers or wildcard globs (``*``, ``**``) to exclude from diffing.
        array_keys: Identity key name (e.g. ``"id"``) or mapping of ``{pointer_pattern: key_name}``
            used to align object elements across array insertions, deletions, and edits.
        float_epsilon: Numeric tolerance when comparing floats/ints (``0.0`` means exact equality).
        normalize: Convert dataclasses, tuples, mappings, and enums into JSON structures first.
        max_lcs_cells: Upper bound on DP cells ``len(a) * len(b)`` before falling back to linear scan.
    """
    src_val: JsonValue = normalize_json(source) if normalize else source
    dst_val: JsonValue = normalize_json(target) if normalize else target

    engine = _DiffEngine(
        detect_moves=detect_moves,
        ignore_patterns=tuple(ignore),
        array_keys=array_keys,
        float_epsilon=float_epsilon,
        max_lcs_cells=max_lcs_cells,
    )
    ops = engine.compute(src_val, dst_val)

    if detect_moves or detect_copies:
        ops = _optimize_structural_ops(
            src_val,
            ops,
            detect_moves=detect_moves,
            detect_copies=detect_copies,
        )

    return Patch(ops)


def diff_reversible(
    source: Any,
    target: Any,
    *,
    include_tests: bool = True,
    detect_moves: bool = True,
    detect_copies: bool = False,
    ignore: Iterable[str | Pointer] = (),
    array_keys: Mapping[str, str] | str | None = None,
    float_epsilon: float = 0.0,
    normalize: bool = False,
    max_lcs_cells: int = 65_536,
) -> ReversiblePatch:
    """Compute both forward and exact reverse RFC 6902 patches between ``source`` and ``target``."""
    src_val: JsonValue = normalize_json(source) if normalize else source
    dst_val: JsonValue = normalize_json(target) if normalize else target

    forward = diff(
        src_val,
        dst_val,
        detect_moves=detect_moves,
        detect_copies=detect_copies,
        ignore=ignore,
        array_keys=array_keys,
        float_epsilon=float_epsilon,
        normalize=False,
        max_lcs_cells=max_lcs_cells,
    )
    reverse = forward.invert_against(src_val, include_tests=include_tests)
    return ReversiblePatch(forward=forward, reverse=reverse)


class _DiffEngine:
    __slots__ = (
        "array_keys",
        "detect_moves",
        "float_epsilon",
        "ignore_patterns",
        "max_lcs_cells",
        "ops",
    )

    def __init__(
        self,
        *,
        detect_moves: bool,
        ignore_patterns: tuple[str | Pointer, ...],
        array_keys: Mapping[str, str] | str | None,
        float_epsilon: float,
        max_lcs_cells: int,
    ) -> None:
        self.detect_moves = detect_moves
        self.ignore_patterns = ignore_patterns
        self.array_keys = array_keys
        self.float_epsilon = float_epsilon
        self.max_lcs_cells = max_lcs_cells
        self.ops: list[PatchOp] = []

    def compute(self, src: JsonValue, dst: JsonValue) -> list[PatchOp]:
        self.ops = []
        self._diff_at(src, dst, Pointer.root())
        return self.ops

    def _is_ignored(self, path: Pointer) -> bool:
        return any(path.matches(pat) for pat in self.ignore_patterns)

    def _resolve_array_key(self, path: Pointer) -> str | None:
        if self.array_keys is None:
            return None
        if isinstance(self.array_keys, str):
            return self.array_keys
        for pattern, key_name in self.array_keys.items():
            if path.matches(pattern):
                return key_name
        return None

    def _diff_at(self, src: JsonValue, dst: JsonValue, path: Pointer) -> None:
        if self._is_ignored(path):
            return

        if json_equal(src, dst, float_epsilon=self.float_epsilon):
            return

        if isinstance(src, dict) and isinstance(dst, dict):
            self._diff_object(src, dst, path)
            return

        if isinstance(src, list) and isinstance(dst, list):
            self._diff_array(src, dst, path)
            return

        self.ops.append(PatchOp(OpKind.REPLACE, path, clone_json(dst)))

    def _diff_object(self, src: JsonObject, dst: JsonObject, path: Pointer) -> None:
        removed_keys = [
            k for k in sorted(src.keys() - dst.keys()) if not self._is_ignored(path / k)
        ]
        added_keys = [
            k for k in sorted(dst.keys() - src.keys()) if not self._is_ignored(path / k)
        ]
        common_keys = sorted(src.keys() & dst.keys())

        moved_removed: set[str] = set()
        moves_by_added: dict[str, str] = {}

        if self.detect_moves and removed_keys and added_keys:
            bucket: dict[Hashable, list[str]] = {}
            for rk in removed_keys:
                h = canonical_key(src[rk])
                bucket.setdefault(h, []).append(rk)

            for ak in added_keys:
                h = canonical_key(dst[ak])
                candidates = bucket.get(h)
                if candidates:
                    rk = candidates.pop(0)
                    moved_removed.add(rk)
                    moves_by_added[ak] = rk

        for rk in removed_keys:
            if rk not in moved_removed:
                self.ops.append(PatchOp(OpKind.REMOVE, path / rk))

        for ak in added_keys:
            if ak in moves_by_added:
                rk = moves_by_added[ak]
                self.ops.append(
                    PatchOp(OpKind.MOVE, path=path / ak, from_path=path / rk)
                )
            else:
                self.ops.append(PatchOp(OpKind.ADD, path / ak, clone_json(dst[ak])))

        for ck in common_keys:
            self._diff_at(src[ck], dst[ck], path / ck)

    def _diff_array(self, src: list[JsonValue], dst: list[JsonValue], path: Pointer) -> None:
        n_total = len(src)
        m_total = len(dst)

        prefix = 0
        limit = min(n_total, m_total)
        while prefix < limit and json_equal(
            src[prefix], dst[prefix], float_epsilon=self.float_epsilon
        ):
            prefix += 1

        suffix = 0
        rem_limit = min(n_total - prefix, m_total - prefix)
        while suffix < rem_limit and json_equal(
            src[n_total - 1 - suffix],
            dst[m_total - 1 - suffix],
            float_epsilon=self.float_epsilon,
        ):
            suffix += 1

        s_mid = src[prefix : n_total - suffix]
        d_mid = dst[prefix : m_total - suffix]
        n = len(s_mid)
        m = len(d_mid)

        if n == 0 and m == 0:
            return

        if n == 0:
            cur_idx = prefix
            for item in d_mid:
                elem_path = path / cur_idx
                if not self._is_ignored(elem_path):
                    self.ops.append(PatchOp(OpKind.ADD, elem_path, clone_json(item)))
                    cur_idx += 1
            return

        if m == 0:
            for offset in range(n - 1, -1, -1):
                elem_path = path / (prefix + offset)
                if not self._is_ignored(elem_path):
                    self.ops.append(PatchOp(OpKind.REMOVE, elem_path))
            return

        id_key = self._resolve_array_key(path)
        anchors = self._compute_lcs_anchors(s_mid, d_mid, id_key=id_key)

        cur_idx = prefix
        prev_s = 0
        prev_d = 0

        for anc_s, anc_d in [*anchors, (n, m)]:
            s_gap = s_mid[prev_s:anc_s]
            d_gap = d_mid[prev_d:anc_d]
            gs = len(s_gap)
            gd = len(d_gap)
            paired = min(gs, gd)

            for k in range(paired):
                self._diff_at(s_gap[k], d_gap[k], path / cur_idx)
                cur_idx += 1

            if gs > gd:
                for _ in range(gs - gd):
                    elem_path = path / cur_idx
                    if not self._is_ignored(elem_path):
                        self.ops.append(PatchOp(OpKind.REMOVE, elem_path))
                    else:
                        cur_idx += 1
            elif gd > gs:
                for item in d_gap[paired:]:
                    elem_path = path / cur_idx
                    if not self._is_ignored(elem_path):
                        self.ops.append(PatchOp(OpKind.ADD, elem_path, clone_json(item)))
                        cur_idx += 1

            if (anc_s, anc_d) != (n, m):
                self._diff_at(s_mid[anc_s], d_mid[anc_d], path / cur_idx)
                cur_idx += 1
                prev_s = anc_s + 1
                prev_d = anc_d + 1

    def _compute_lcs_anchors(
        self,
        s_mid: list[JsonValue],
        d_mid: list[JsonValue],
        *,
        id_key: str | None,
    ) -> list[tuple[int, int]]:
        n = len(s_mid)
        m = len(d_mid)
        if n * m > self.max_lcs_cells:
            return []

        use_fast_keys = self.float_epsilon == 0.0
        s_keys: list[Hashable | None] = []
        d_keys: list[Hashable | None] = []

        if use_fast_keys:
            for item in s_mid:
                if id_key is not None and isinstance(item, dict) and id_key in item:
                    s_keys.append(("__id__", canonical_key(item[id_key])))
                else:
                    s_keys.append(canonical_key(item))
            for item in d_mid:
                if id_key is not None and isinstance(item, dict) and id_key in item:
                    d_keys.append(("__id__", canonical_key(item[id_key])))
                else:
                    d_keys.append(canonical_key(item))

        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(n - 1, -1, -1):
            row = dp[i]
            next_row = dp[i + 1]
            for j in range(m - 1, -1, -1):
                if use_fast_keys:
                    matched = s_keys[i] == d_keys[j]
                else:
                    matched = self._elements_match_slow(s_mid[i], d_mid[j], id_key=id_key)

                if matched:
                    row[j] = 1 + next_row[j + 1]
                else:
                    a = next_row[j]
                    b = row[j + 1]
                    row[j] = a if a >= b else b

        anchors: list[tuple[int, int]] = []
        i = 0
        j = 0
        while i < n and j < m:
            if use_fast_keys:
                matched = s_keys[i] == d_keys[j]
            else:
                matched = self._elements_match_slow(s_mid[i], d_mid[j], id_key=id_key)

            if matched:
                anchors.append((i, j))
                i += 1
                j += 1
            elif dp[i + 1][j] >= dp[i][j + 1]:
                i += 1
            else:
                j += 1

        return anchors

    def _elements_match_slow(
        self, a: JsonValue, b: JsonValue, *, id_key: str | None
    ) -> bool:
        if id_key is not None and isinstance(a, dict) and isinstance(b, dict):
            if id_key in a and id_key in b:
                return json_equal(a[id_key], b[id_key], float_epsilon=0.0)
        return json_equal(a, b, float_epsilon=self.float_epsilon)


def _is_pure_object_path(doc: JsonValue, path: Pointer) -> bool:
    """Return True if every container traversed along ``path`` in ``doc`` is a JSON object."""
    cur: Any = doc
    for token in path.tokens[:-1]:
        if not isinstance(cur, dict) or token not in cur:
            return False
        cur = cur[token]
    return isinstance(cur, dict)


def _paths_interfere(a: Pointer, b: Pointer) -> bool:
    """Return True if two pointers are equal or one is an ancestor of the other."""
    return a == b or a.is_proper_prefix_of(b) or b.is_proper_prefix_of(a)


def _index_structural_subtrees(
    node: JsonValue,
    path: Pointer,
    out: dict[Hashable, Pointer],
) -> None:
    if isinstance(node, dict):
        if node:
            h = canonical_key(node)
            out.setdefault(h, path)
            for k in sorted(node):
                _index_structural_subtrees(node[k], path / k, out)
    elif isinstance(node, list):
        if node:
            h = canonical_key(node)
            out.setdefault(h, path)
            for idx, item in enumerate(node):
                _index_structural_subtrees(item, path / idx, out)


def _optimize_structural_ops(
    source: JsonValue,
    ops: list[PatchOp],
    *,
    detect_moves: bool,
    detect_copies: bool,
) -> list[PatchOp]:
    """Safely upgrade cross-object remove+add pairs to ``move`` and structural adds to ``copy``."""
    working = clone_json(source)
    optimized = list(ops)

    if detect_moves:
        # Pair pure-object REMOVE at idx_r with pure-object ADD at idx_a when no intervening op
        # touches either pointer's lineage.
        removed_slots: list[tuple[int, Pointer, Hashable]] = []
        sim = clone_json(source)
        slot_states: list[JsonValue] = []

        for idx, op in enumerate(optimized):
            slot_states.append(clone_json(sim))
            if op.op is OpKind.REMOVE and _is_pure_object_path(sim, op.path):
                val = op.path.resolve(sim)
                removed_slots.append((idx, op.path, canonical_key(val)))
            sim = Patch([op]).apply(sim)

        consumed: set[int] = set()
        replacements: dict[int, PatchOp | None] = {}

        for add_idx, op in enumerate(optimized):
            if op.op is not OpKind.ADD or add_idx in consumed:
                continue
            state_at_add = slot_states[add_idx]
            if not _is_pure_object_path(state_at_add, op.path):
                continue

            target_hash = canonical_key(op.value)
            for rem_idx, rem_path, rem_hash in removed_slots:
                if rem_idx in consumed or rem_hash != target_hash:
                    continue
                if _paths_interfere(rem_path, op.path):
                    continue

                lo, hi = (rem_idx, add_idx) if rem_idx < add_idx else (add_idx, rem_idx)
                tracked = rem_path if rem_idx < add_idx else rem_path
                interfering = False
                for mid in range(lo + 1, hi):
                    if mid in consumed or replacements.get(mid) is None:
                        continue
                    mid_op = replacements.get(mid, optimized[mid])
                    assert mid_op is not None
                    if _paths_interfere(mid_op.path, tracked) or (
                        mid_op.from_path is not None
                        and _paths_interfere(mid_op.from_path, tracked)
                    ):
                        interfering = True
                        break
                    if rem_idx > add_idx and (
                        _paths_interfere(mid_op.path, op.path)
                        or (
                            mid_op.from_path is not None
                            and _paths_interfere(mid_op.from_path, op.path)
                        )
                    ):
                        interfering = True
                        break

                if not interfering:
                    consumed.add(rem_idx)
                    consumed.add(add_idx)
                    if rem_idx < add_idx:
                        replacements[rem_idx] = None
                        replacements[add_idx] = PatchOp(
                            OpKind.MOVE, path=op.path, from_path=rem_path
                        )
                    else:
                        replacements[add_idx] = PatchOp(
                            OpKind.MOVE, path=op.path, from_path=rem_path
                        )
                        replacements[rem_idx] = None
                    break

        if replacements:
            next_ops: list[PatchOp] = []
            for idx, orig in enumerate(optimized):
                if idx in replacements:
                    rep = replacements[idx]
                    if rep is not None:
                        next_ops.append(rep)
                else:
                    next_ops.append(orig)
            optimized = next_ops

    if detect_copies:
        final_ops: list[PatchOp] = []
        for op in optimized:
            if (
                op.op is OpKind.ADD
                and isinstance(op.value, (dict, list))
                and len(op.value) > 0
            ):
                index: dict[Hashable, Pointer] = {}
                _index_structural_subtrees(working, Pointer.root(), index)
                match_ptr = index.get(canonical_key(op.value))
                if match_ptr is not None and not match_ptr.is_proper_prefix_of(op.path):
                    copy_op = PatchOp(OpKind.COPY, path=op.path, from_path=match_ptr)
                    working = Patch([copy_op]).apply(working)
                    final_ops.append(copy_op)
                    continue

            working = Patch([op]).apply(working)
            final_ops.append(op)
        optimized = final_ops

    return optimized
