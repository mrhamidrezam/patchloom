"""Tests for RFC 6901 JSON Pointer parsing, resolution, updates, and glob matching."""

from __future__ import annotations

import pytest

from patchloom import Pointer, PointerError, escape_token, resolve_pointer, unescape_token


def test_rfc6901_section5_official_vectors() -> None:
    document = {
        "foo": ["bar", "baz"],
        "": 0,
        "a/b": 1,
        "c%d": 2,
        "e^f": 3,
        "g|h": 4,
        "i\\j": 5,
        'k"l': 6,
        " ": 7,
        "m~n": 8,
    }

    assert resolve_pointer(document, "") == document
    assert resolve_pointer(document, "/foo") == ["bar", "baz"]
    assert resolve_pointer(document, "/foo/0") == "bar"
    assert resolve_pointer(document, "/") == 0
    assert resolve_pointer(document, "/a~1b") == 1
    assert resolve_pointer(document, "/c%d") == 2
    assert resolve_pointer(document, "/e^f") == 3
    assert resolve_pointer(document, "/g|h") == 4
    assert resolve_pointer(document, "/i\\j") == 5
    assert resolve_pointer(document, '/k"l') == 6
    assert resolve_pointer(document, "/ ") == 7
    assert resolve_pointer(document, "/m~0n") == 8


def test_rfc6901_uri_fragment_identifiers() -> None:
    doc = {"foo": ["bar", "baz"], "a/b": 1, "c%d": 2, "m~n": 8}
    assert resolve_pointer(doc, "#") == doc
    assert resolve_pointer(doc, "#/foo/0") == "bar"
    assert resolve_pointer(doc, "#/a~1b") == 1
    assert resolve_pointer(doc, "#/c%25d") == 2
    assert resolve_pointer(doc, "#/m~0n") == 8


def test_tilde_unescape_order_trap() -> None:
    # '~01' MUST unescape to '~1', NEVER '/' (RFC 6901 §4)
    assert unescape_token("~01") == "~1"
    assert escape_token("~1") == "~01"
    doc = {"~1": "correct", "/": "wrong"}
    assert resolve_pointer(doc, "/~01") == "correct"


def test_invalid_pointer_syntax_and_array_indices() -> None:
    with pytest.raises(PointerError):
        Pointer.parse("missing-leading-slash")

    doc = {"items": ["a", "b", "c"]}
    with pytest.raises(PointerError):
        resolve_pointer(doc, "/items/01")  # Leading zero forbidden
    with pytest.raises(PointerError):
        resolve_pointer(doc, "/items/-")  # '-' not readable
    with pytest.raises(PointerError):
        resolve_pointer(doc, "/items/3")  # Out of bounds
    with pytest.raises(PointerError):
        resolve_pointer(doc, "/items/-1")  # Negative forbidden


def test_pointer_immutable_set_delete_and_navigation() -> None:
    original = {"users": [{"name": "Ada"}]}
    ptr = Pointer.root() / "users" / 0 / "role"
    assert str(ptr) == "/users/0/role"
    assert ptr.parent == Pointer.parse("/users/0")
    assert ptr.leaf == "role"

    updated = ptr.set(original, "architect")
    assert original == {"users": [{"name": "Ada"}]}
    assert updated == {"users": [{"name": "Ada", "role": "architect"}]}

    appended = Pointer.parse("/users/-").set(updated, {"name": "Grace"})
    assert len(appended["users"]) == 2  # type: ignore[index]

    deleted = ptr.delete(updated)
    assert deleted == original
    assert resolve_pointer(original, "/users/99", default="fallback") == "fallback"


def test_pointer_wildcard_glob_matching() -> None:
    ptr = Pointer.parse("/services/api/credentials/token")
    assert ptr.matches("/services/*/credentials/token")
    assert ptr.matches("**/token")
    assert ptr.matches("/services/**/token")
    assert not ptr.matches("/services/*/token")
    assert Pointer.root().matches("")
    assert Pointer.root().matches("**")
