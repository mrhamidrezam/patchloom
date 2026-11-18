"""RFC 6901 JSON Pointer parsing, traversal, immutable updates, and glob matching."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Any, Self, overload

from patchloom._core import JsonValue, clone_json
from patchloom.errors import PointerError

_MISSING = object()


def escape_token(token: str | int) -> str:
    """Escape a single reference token according to RFC 6901 §3 (``~`` -> ``~0``, ``/`` -> ``~1``)."""
    text = str(token)
    return text.replace("~", "~0").replace("/", "~1")


def unescape_token(token: str) -> str:
    """Unescape a single reference token according to RFC 6901 §4 (``~1`` -> ``/``, ``~0`` -> ``~``)."""
    return token.replace("~1", "/").replace("~0", "~")


def parse_array_index(token: str, length: int, *, allow_end: bool = False) -> int:
    """Parse and validate an RFC 6901 array index token against an array of ``length``."""
    if token == "-":
        if allow_end:
            return length
        raise PointerError("Array end token '-' is not allowed when reading or removing an element.")

    if not token or not token.isascii() or not token.isdigit():
        raise PointerError(f"Invalid array index token {token!r}.")

    if len(token) > 1 and token.startswith("0"):
        raise PointerError(f"Leading zeros are forbidden in RFC 6901 array index {token!r}.")

    index = int(token)
    limit = length if allow_end else length - 1
    if index < 0 or index > limit:
        raise PointerError(
            f"Array index {index} out of bounds for array of length {length}."
        )
    return index


@dataclass(frozen=True, slots=True)
class Pointer:
    """Immutable RFC 6901 JSON Pointer.

    Examples:
        >>> ptr = Pointer.parse("/users/0/profile~1name")
        >>> ptr.tokens
        ('users', '0', 'profile/name')
        >>> str(ptr)
        '/users/0/profile~1name'
        >>> str(Pointer.root() / "items" / 2)
        '/items/2'
    """

    tokens: tuple[str, ...] = ()

    @classmethod
    def root(cls) -> Self:
        """Return the root pointer ``""``."""
        return cls(())

    @classmethod
    def parse(cls, value: str | Self | Sequence[str | int]) -> Self:
        """Parse an RFC 6901 JSON Pointer string, URI fragment, or sequence of tokens."""
        if isinstance(value, cls):
            return value

        if isinstance(value, str):
            if value.startswith("#"):
                # RFC 6901 §6 URI fragment identifier representation (basic '#' prefix support)
                from urllib.parse import unquote

                value = unquote(value[1:])

            if value == "":
                return cls(())
            if not value.startswith("/"):
                raise PointerError(
                    f"Non-empty JSON Pointer must start with '/', got {value!r}."
                )
            raw_parts = value.split("/")[1:]
            return cls(tuple(unescape_token(part) for part in raw_parts))

        if isinstance(value, Sequence):
            return cls(tuple(str(item) for item in value))

        raise PointerError(f"Cannot construct Pointer from {type(value).__name__!r}.")

    @property
    def is_root(self) -> bool:
        """Return ``True`` if this pointer addresses the root document ``""``."""
        return len(self.tokens) == 0

    @property
    def parent(self) -> Pointer:
        """Return the parent pointer, raising ``PointerError`` on the root pointer."""
        if not self.tokens:
            raise PointerError("Root pointer '' has no parent.")
        return Pointer(self.tokens[:-1])

    @property
    def leaf(self) -> str:
        """Return the final unescaped reference token, raising ``PointerError`` on root."""
        if not self.tokens:
            raise PointerError("Root pointer '' has no leaf token.")
        return self.tokens[-1]

    def is_proper_prefix_of(self, other: Pointer | str) -> bool:
        """Return ``True`` if ``self`` is a strict ancestor of ``other`` (RFC 6902 §4.4)."""
        target = Pointer.parse(other)
        if len(self.tokens) >= len(target.tokens):
            return False
        return target.tokens[: len(self.tokens)] == self.tokens

    def child(self, token: str | int) -> Pointer:
        """Append a single child key or index token."""
        return Pointer((*self.tokens, str(token)))

    def __truediv__(self, token: str | int) -> Pointer:
        return self.child(token)

    def __len__(self) -> int:
        return len(self.tokens)

    def __iter__(self) -> Iterator[str]:
        return iter(self.tokens)

    def __str__(self) -> str:
        if not self.tokens:
            return ""
        return "/" + "/".join(escape_token(t) for t in self.tokens)

    def resolve(self, document: JsonValue, default: Any = _MISSING) -> Any:
        """Evaluate this JSON Pointer against ``document`` per RFC 6901 §4."""
        current: Any = document
        for depth, token in enumerate(self.tokens):
            if isinstance(current, dict):
                if token not in current:
                    if default is not _MISSING:
                        return default
                    traversed = Pointer(self.tokens[: depth + 1])
                    raise PointerError(f"Key {token!r} not found at pointer '{traversed}'.")
                current = current[token]
            elif isinstance(current, list):
                try:
                    idx = parse_array_index(token, len(current), allow_end=False)
                except PointerError:
                    if default is not _MISSING:
                        return default
                    raise
                current = current[idx]
            else:
                if default is not _MISSING:
                    return default
                traversed = Pointer(self.tokens[:depth])
                raise PointerError(
                    f"Cannot traverse token {token!r} inside scalar at '{traversed}'."
                )
        return current

    def exists(self, document: JsonValue) -> bool:
        """Return ``True`` if this pointer resolves to an existing location in ``document``."""
        sentinel = object()
        return self.resolve(document, default=sentinel) is not sentinel

    def resolve_parent(self, document: JsonValue) -> tuple[dict[str, JsonValue] | list[JsonValue], str]:
        """Resolve the immediate container (dict or list) and leaf token for mutation."""
        if self.is_root:
            raise PointerError("Root pointer '' has no parent container.")
        container = self.parent.resolve(document)
        if not isinstance(container, (dict, list)):
            raise PointerError(
                f"Parent pointer '{self.parent}' resolved to {type(container).__name__}, expected object or array."
            )
        return container, self.leaf

    def set(self, document: JsonValue, value: JsonValue) -> JsonValue:
        """Return a new document with ``value`` set (added or replaced) at this pointer."""
        cloned_value = clone_json(value)
        if self.is_root:
            return cloned_value
        doc_copy = clone_json(document)
        container, leaf = self.resolve_parent(doc_copy)
        if isinstance(container, dict):
            container[leaf] = cloned_value
        else:
            if leaf == "-":
                container.append(cloned_value)
            else:
                idx = parse_array_index(leaf, len(container), allow_end=True)
                if idx == len(container):
                    container.append(cloned_value)
                else:
                    container[idx] = cloned_value
        return doc_copy

    def delete(self, document: JsonValue) -> JsonValue:
        """Return a new document with the value at this pointer removed."""
        if self.is_root:
            raise PointerError("Cannot delete the root document pointer ''.")
        doc_copy = clone_json(document)
        container, leaf = self.resolve_parent(doc_copy)
        if isinstance(container, dict):
            if leaf not in container:
                raise PointerError(f"Key {leaf!r} does not exist at '{self}'.")
            del container[leaf]
        else:
            idx = parse_array_index(leaf, len(container), allow_end=False)
            del container[idx]
        return doc_copy

    def matches(self, pattern: str | Pointer) -> bool:
        """Match this pointer against an exact pointer or wildcard glob pattern.

        Supported wildcards per segment:
          - ``*`` matches exactly one reference token (key or array index).
          - ``**`` matches zero or more reference tokens across any depth.
        """
        if isinstance(pattern, Pointer):
            return self == pattern

        if pattern == "":
            return self.is_root
        if pattern == "**":
            return True

        normalized = pattern if pattern.startswith("/") else "/" + pattern
        pat_tokens = tuple(unescape_token(p) for p in normalized.split("/")[1:])
        return _match_tokens(self.tokens, pat_tokens)


@overload
def resolve_pointer(document: JsonValue, pointer: str | Pointer) -> JsonValue: ...


@overload
def resolve_pointer[D](document: JsonValue, pointer: str | Pointer, default: D) -> JsonValue | D: ...


def resolve_pointer(document: JsonValue, pointer: str | Pointer, default: Any = _MISSING) -> Any:
    """Convenience function to evaluate an RFC 6901 JSON Pointer against ``document``."""
    return Pointer.parse(pointer).resolve(document, default=default)


def _match_tokens(tokens: tuple[str, ...], pattern: tuple[str, ...]) -> bool:
    t_len = len(tokens)
    p_len = len(pattern)

    memo: dict[tuple[int, int], bool] = {}

    def _step(ti: int, pi: int) -> bool:
        state = (ti, pi)
        if state in memo:
            return memo[state]

        if pi == p_len:
            ans = ti == t_len
        elif pattern[pi] == "**":
            # '**' matches zero tokens (pi + 1) or consumes one token (ti + 1)
            ans = _step(ti, pi + 1) or (ti < t_len and _step(ti + 1, pi))
        elif ti < t_len and (pattern[pi] == "*" or pattern[pi] == tokens[ti]):
            ans = _step(ti + 1, pi + 1)
        else:
            ans = False

        memo[state] = ans
        return ans

    return _step(0, 0)
