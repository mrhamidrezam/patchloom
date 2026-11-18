"""Tests for RFC 6902 JSON Patch operations, atomicity, strict equality, and inversion."""

from __future__ import annotations

import pytest

from patchloom import (
    Patch,
    PatchOperationError,
    PatchTestFailedError,
    apply_patch,
    invert_patch,
    json_equal,
)


def test_rfc6902_appendix_a_official_examples() -> None:
    # A.1. Adding an Object Member
    assert apply_patch(
        {"foo": "bar"},
        [{"op": "add", "path": "/baz", "value": "qux"}],
    ) == {"baz": "qux", "foo": "bar"}

    # A.2. Adding an Array Element
    assert apply_patch(
        {"foo": ["bar", "baz"]},
        [{"op": "add", "path": "/foo/1", "value": "qux"}],
    ) == {"foo": ["bar", "qux", "baz"]}

    # A.3. Removing an Object Member
    assert apply_patch(
        {"baz": "qux", "foo": "bar"},
        [{"op": "remove", "path": "/baz"}],
    ) == {"foo": "bar"}

    # A.4. Removing an Array Element
    assert apply_patch(
        {"foo": ["bar", "qux", "baz"]},
        [{"op": "remove", "path": "/foo/1"}],
    ) == {"foo": ["bar", "baz"]}

    # A.5. Replacing a Value
    assert apply_patch(
        {"baz": "qux", "foo": "bar"},
        [{"op": "replace", "path": "/baz", "value": "boo"}],
    ) == {"baz": "boo", "foo": "bar"}

    # A.6. Moving a Value
    assert apply_patch(
        {"foo": {"bar": "baz", "waldo": "fred"}, "qux": {"corge": "grault"}},
        [{"op": "move", "from": "/foo/waldo", "path": "/qux/thud"}],
    ) == {"foo": {"bar": "baz"}, "qux": {"corge": "grault", "thud": "fred"}}

    # A.7. Moving an Array Element
    assert apply_patch(
        {"foo": ["all", "grass", "cows", "eat"]},
        [{"op": "move", "from": "/foo/1", "path": "/foo/3"}],
    ) == {"foo": ["all", "cows", "eat", "grass"]}

    # A.8 & A.9. Testing a Value: Success & Error
    doc_a8 = {"baz": "qux", "foo": ["a", 2, "c"]}
    assert apply_patch(
        doc_a8,
        [
            {"op": "test", "path": "/baz", "value": "qux"},
            {"op": "test", "path": "/foo/1", "value": 2},
        ],
    ) == doc_a8

    with pytest.raises(PatchTestFailedError):
        apply_patch({"baz": "qux"}, [{"op": "test", "path": "/baz", "value": "bar"}])

    # A.10. Adding a Nested Member Object
    assert apply_patch(
        {"foo": "bar"},
        [{"op": "add", "path": "/child", "value": {"grandchild": {}}}],
    ) == {"foo": "bar", "child": {"grandchild": {}}}

    # A.11. Ignoring Unrecognized Elements in Operation Object
    assert apply_patch(
        {"foo": "bar"},
        [{"op": "add", "path": "/baz", "value": "qux", "xyz": 123}],
    ) == {"foo": "bar", "baz": "qux"}

    # A.12. Adding to a Nonexistent Target
    with pytest.raises(PatchOperationError):
        apply_patch({"foo": "bar"}, [{"op": "add", "path": "/baz/bat", "value": "qux"}])

    # A.14. ~ Escape Ordering
    assert apply_patch(
        {"/": 9, "~1": 10},
        [{"op": "test", "path": "/~01", "value": 10}],
    ) == {"/": 9, "~1": 10}

    # A.15. Comparing Strings and Numbers
    with pytest.raises(PatchTestFailedError):
        apply_patch(
            {"/": 9, "~1": 10},
            [{"op": "test", "path": "/~01", "value": "10"}],
        )

    # A.16. Adding an Array Value with '-'
    assert apply_patch(
        {"foo": ["bar"]},
        [{"op": "add", "path": "/foo/-", "value": ["abc", "def"]}],
    ) == {"foo": ["bar", ["abc", "def"]]}


def test_strict_boolean_vs_numeric_test_semantics() -> None:
    doc = {"flag": True, "zero": 0, "count": 1}
    with pytest.raises(PatchTestFailedError):
        apply_patch(doc, [{"op": "test", "path": "/flag", "value": 1}])
    with pytest.raises(PatchTestFailedError):
        apply_patch(doc, [{"op": "test", "path": "/zero", "value": False}])

    # Numeric 1 and 1.0 ARE equal in JSON (RFC 6902 §4.6)
    assert apply_patch(doc, [{"op": "test", "path": "/count", "value": 1.0}]) == doc


def test_transactional_atomicity_on_mid_patch_failure() -> None:
    original = {"balance": 100, "items": ["a", "b"]}
    snapshot = {"balance": 100, "items": ["a", "b"]}

    failing_ops = [
        {"op": "replace", "path": "/balance", "value": 50},
        {"op": "add", "path": "/items/-", "value": "c"},
        {"op": "test", "path": "/balance", "value": 999},  # Fails!
    ]

    with pytest.raises(PatchTestFailedError):
        apply_patch(original, failing_ops, in_place=True)

    assert original == snapshot


def test_move_proper_prefix_rejected_and_ancestor_overwrite_inverted() -> None:
    with pytest.raises(PatchOperationError):
        apply_patch({"a": {"b": 1}}, [{"op": "move", "from": "/a", "path": "/a/b/c"}])

    # Moving a descendant onto its own ancestor overwrites the ancestor cleanly and is invertible!
    doc = {"a": {"b": 42, "sibling": "keep"}}
    patch = Patch([{"op": "move", "from": "/a/b", "path": "/a"}])
    after = patch.apply(doc)
    assert after == {"a": 42}

    inv = invert_patch(doc, patch)
    restored = inv.apply(after)
    assert json_equal(restored, doc)


def test_invert_patch_across_all_operation_types() -> None:
    doc = {
        "title": "v1",
        "tags": ["alpha", "beta", "gamma"],
        "meta": {"owner": "ops", "deprecated": True},
        "existing": "old",
    }
    patch = Patch(
        [
            {"op": "replace", "path": "/title", "value": "v2"},
            {"op": "add", "path": "/tags/-", "value": "delta"},
            {"op": "move", "from": "/tags/0", "path": "/tags/2"},
            {"op": "copy", "from": "/meta", "path": "/meta_backup"},
            {"op": "remove", "path": "/meta/deprecated"},
            {"op": "add", "path": "/existing", "value": "overwritten"},
            {"op": "test", "path": "/title", "value": "v2"},
        ]
    )

    transformed = patch.apply(doc)
    inverse = patch.invert_against(doc, include_tests=True)
    rolled_back = inverse.apply(transformed)
    assert json_equal(rolled_back, doc)
