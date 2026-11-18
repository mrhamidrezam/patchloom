"""Tests for RFC 7396 JSON Merge Patch official vectors and generation."""

from __future__ import annotations

import pytest

from patchloom import apply_merge_patch, create_merge_patch, json_equal


@pytest.mark.parametrize(
    ("original", "patch", "expected"),
    [
        ({"a": "b"}, {"a": "c"}, {"a": "c"}),
        ({"a": "b"}, {"b": "c"}, {"a": "b", "b": "c"}),
        ({"a": "b"}, {"a": None}, {}),
        ({"a": "b", "b": "c"}, {"a": None}, {"b": "c"}),
        ({"a": ["b"]}, {"a": "c"}, {"a": "c"}),
        ({"a": "c"}, {"a": ["b"]}, {"a": ["b"]}),
        ({"a": {"b": "c"}}, {"a": {"b": "d", "c": None}}, {"a": {"b": "d"}}),
        ({"a": [{"b": "c"}]}, {"a": [1]}, {"a": [1]}),
        (["a", "b"], ["c", "d"], ["c", "d"]),
        ({"a": "b"}, ["c"], ["c"]),
        ({"a": "foo"}, None, None),
        ({"a": "foo"}, "bar", "bar"),
        ({"e": None}, {"a": 1}, {"e": None, "a": 1}),
        ([1, 2], {"a": "b", "c": None}, {"a": "b"}),
        ({}, {"a": {"bb": {"ccc": None}}}, {"a": {"bb": {}}}),
    ],
)
def test_rfc7396_appendix_a_test_cases(original, patch, expected) -> None:
    result = apply_merge_patch(original, patch)
    assert json_equal(result, expected)


def test_create_merge_patch_roundtrip_and_null_limitation() -> None:
    src = {"title": "Goodbye!", "author": {"givenName": "John", "familyName": "Doe"}, "tags": ["example", "sample"]}
    dst = {"title": "Hello!", "author": {"givenName": "John"}, "phoneNumber": "+01-123-456-7890", "tags": ["example"]}

    mp = create_merge_patch(src, dst)
    assert mp == {
        "author": {"familyName": None},
        "phoneNumber": "+01-123-456-7890",
        "tags": ["example"],
        "title": "Hello!",
    }
    assert apply_merge_patch(src, mp) == dst

    with pytest.raises(ValueError, match="explicit null"):
        create_merge_patch({"a": 1}, {"a": None})
