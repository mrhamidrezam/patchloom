"""Tests for structural diffing, LCS alignment, move/copy optimization, and reversible deltas."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import StrEnum

import pytest

from patchloom import (
    OpKind,
    PatchTestFailedError,
    diff,
    diff_reversible,
    json_equal,
)
from patchloom._core import JsonValue


def test_array_lcs_minimal_insert_and_delete() -> None:
    src = ["a", "b", "c", "d", "e"]
    dst = ["a", "x", "b", "d", "e", "z"]

    patch = diff(src, dst)
    assert patch.apply(src) == dst
    # 'b', 'd', 'e' remain untouched via LCS; only 'x' added, 'c' removed, 'z' added
    assert len(patch) == 3


def test_keyed_object_array_diff() -> None:
    src = [
        {"id": "u1", "role": "viewer", "active": True},
        {"id": "u2", "role": "editor", "active": True},
    ]
    dst = [
        {"id": "u0", "role": "guest", "active": True},
        {"id": "u1", "role": "admin", "active": True},
        {"id": "u2", "role": "editor", "active": True},
    ]

    patch = diff(src, dst, array_keys="id")
    assert patch.apply(src) == dst
    assert patch.to_list() == [
        {"op": "add", "path": "/0", "value": {"id": "u0", "role": "guest", "active": True}},
        {"op": "replace", "path": "/1/role", "value": "admin"},
    ]


def test_move_and_copy_detection() -> None:
    src = {
        "legacy_config": {"retries": 5, "backoff": [1, 2, 4]},
        "defaults": {"region": "eu-central-1", "tls": True},
        "services": {},
    }
    dst = {
        "runtime_config": {"retries": 5, "backoff": [1, 2, 4]},
        "defaults": {"region": "eu-central-1", "tls": True},
        "services": {
            "billing": {"region": "eu-central-1", "tls": True},
        },
    }

    patch = diff(src, dst, detect_moves=True, detect_copies=True)
    assert json_equal(patch.apply(src), dst)
    kinds = [op.op for op in patch]
    assert OpKind.MOVE in kinds
    assert OpKind.COPY in kinds


def test_ignore_wildcards_and_float_epsilon() -> None:
    src = {
        "score": 0.1 + 0.2,
        "updated_at": "2026-11-17T00:00:00Z",
        "nodes": [{"id": 1, "secret": "old-key", "load": 0.5}],
    }
    dst = {
        "score": 0.3,
        "updated_at": "2026-11-18T10:00:00Z",
        "nodes": [{"id": 1, "secret": "rotated-key", "load": 0.75}],
    }

    patch = diff(
        src,
        dst,
        ignore=["/updated_at", "/nodes/*/secret"],
        float_epsilon=1e-9,
    )
    assert patch.to_list() == [
        {"op": "replace", "path": "/nodes/0/load", "value": 0.75}
    ]


def test_dataclass_and_enum_normalization() -> None:
    class Tier(StrEnum):
        FREE = "free"
        PRO = "pro"

    @dataclass(frozen=True)
    class Account:
        id: int
        tier: Tier
        scopes: tuple[str, ...]

    a = Account(id=7, tier=Tier.FREE, scopes=("read",))
    b = Account(id=7, tier=Tier.PRO, scopes=("read", "write"))

    patch = diff(a, b, normalize=True)
    assert type(patch[1].value) is str
    assert patch.to_list() == [
        {"op": "add", "path": "/scopes/1", "value": "write"},
        {"op": "replace", "path": "/tier", "value": "pro"},
    ]


def test_reversible_patch_rollback_and_optimistic_concurrency_guard() -> None:
    v1 = {"version": 1, "flags": {"beta": False}, "queue": ["job-1", "job-2"]}
    v2 = {"version": 2, "flags": {"beta": True, "dark": True}, "queue": ["job-2", "job-3"]}

    rev = diff_reversible(v1, v2, include_tests=True)
    applied = rev.apply(v1)
    assert json_equal(applied, v2)
    assert json_equal(rev.rollback(applied), v1)

    # Concurrent tampering triggers PatchTestFailedError during rollback
    tampered = {**applied, "version": 99}  # type: ignore[dict-item]
    with pytest.raises(PatchTestFailedError):
        rev.rollback(tampered)


def _random_json(rng: random.Random, depth: int = 0) -> JsonValue:
    if depth >= 3:
        return rng.choice([None, True, False, 0, 1, -5, 2.5, "alpha", "beta"])
    roll = rng.randrange(6)
    if roll == 0:
        return rng.choice([None, True, False])
    if roll == 1:
        return rng.choice([0, 1, 2, 42, 1.0, 3.25])
    if roll == 2:
        return rng.choice(["x", "y", "a/b", "m~n", ""])
    if roll == 3:
        return [_random_json(rng, depth + 1) for _ in range(rng.randrange(5))]
    keys = rng.sample(["a", "b", "c", "d", "e/f", "~k"], k=rng.randrange(5))
    return {k: _random_json(rng, depth + 1) for k in keys}


def test_randomized_property_roundtrips() -> None:
    rng = random.Random(20261118)
    for _ in range(100):
        src = _random_json(rng)
        dst = _random_json(rng)
        rev = diff_reversible(src, dst, detect_moves=True, detect_copies=True)
        forward_res = rev.apply(src)
        assert json_equal(forward_res, dst)
        rollback_res = rev.rollback(forward_res)
        assert json_equal(rollback_res, src)
