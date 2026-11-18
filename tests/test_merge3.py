"""Tests for three-way structural merge, conflict policies, custom resolvers, and keyed arrays."""

from __future__ import annotations

import pytest

from patchloom import (
    MISSING,
    ConflictKind,
    ConflictPolicy,
    MergeConflict,
    MergeConflictError,
    merge3,
)


def test_clean_non_overlapping_three_way_merge() -> None:
    base = {
        "service": "gateway",
        "limits": {"rpm": 100, "burst": 20},
        "deprecated": True,
    }
    ours = {
        "service": "gateway",
        "limits": {"rpm": 250, "burst": 20},
        "deprecated": True,
        "region": "eu-west-1",
    }
    theirs = {
        "service": "gateway",
        "limits": {"rpm": 100, "burst": 50},
        "tls": True,
    }

    res = merge3(base, ours, theirs)
    assert res.is_clean
    assert res.value == {
        "service": "gateway",
        "limits": {"rpm": 250, "burst": 50},
        "region": "eu-west-1",
        "tls": True,
    }


def test_conflict_detection_and_builtin_policies() -> None:
    base = {"timeout": 10, "obsolete": "v1"}
    ours = {"timeout": 30}  # modified timeout, deleted obsolete
    theirs = {"timeout": 60, "obsolete": "v2"}  # modified both

    with pytest.raises(MergeConflictError) as exc_info:
        merge3(base, ours, theirs, policy=ConflictPolicy.RAISE)

    conflicts = exc_info.value.conflicts
    assert len(conflicts) == 2
    by_path = {str(c.path): c for c in conflicts}
    assert by_path["/obsolete"].kind is ConflictKind.DELETE_MODIFY
    assert by_path["/timeout"].kind is ConflictKind.MODIFY_MODIFY

    ours_res = merge3(base, ours, theirs, policy=ConflictPolicy.OURS)
    assert not ours_res.is_clean
    assert ours_res.value == {"timeout": 30}

    theirs_res = merge3(base, ours, theirs, policy=ConflictPolicy.THEIRS)
    assert theirs_res.value == {"timeout": 60, "obsolete": "v2"}

    base_res = merge3(base, ours, theirs, policy=ConflictPolicy.BASE)
    assert base_res.value == {"timeout": 10, "obsolete": "v1"}


def test_custom_conflict_resolver_callable() -> None:
    base = {"replicas": 2, "temp_flag": "old"}
    ours = {"replicas": 4, "temp_flag": "changed"}
    theirs = {"replicas": 6}

    def resolver(conflict: MergeConflict):
        if str(conflict.path) == "/replicas":
            assert isinstance(conflict.ours, int) and isinstance(conflict.theirs, int)
            return max(conflict.ours, conflict.theirs)
        return MISSING  # Drop conflicted temp_flag

    res = merge3(base, ours, theirs, policy=resolver)
    assert res.value == {"replicas": 6}
    assert len(res.conflicts) == 2


def test_keyed_array_three_way_merge() -> None:
    base = {
        "routes": [
            {"id": "r1", "path": "/health", "auth": False},
            {"id": "r2", "path": "/users", "auth": True},
        ]
    }
    ours = {
        "routes": [
            {"id": "r1", "path": "/healthz", "auth": False},
            {"id": "r2", "path": "/users", "auth": True},
            {"id": "r3", "path": "/billing", "auth": True},
        ]
    }
    theirs = {
        "routes": [
            {"id": "r2", "path": "/users", "auth": True, "rate_limit": 100},
            {"id": "r4", "path": "/metrics", "auth": False},
        ]
    }

    # Note: r1 was modified in ours and deleted in theirs -> conflict resolved via OURS
    res = merge3(
        base,
        ours,
        theirs,
        policy=ConflictPolicy.OURS,
        array_keys={"/routes": "id"},
    )
    assert len(res.conflicts) == 1
    assert res.conflicts[0].kind is ConflictKind.MODIFY_DELETE
    assert res.value == {
        "routes": [
            {"id": "r1", "path": "/healthz", "auth": False},
            {"id": "r2", "path": "/users", "auth": True, "rate_limit": 100},
            {"id": "r3", "path": "/billing", "auth": True},
            {"id": "r4", "path": "/metrics", "auth": False},
        ]
    }
