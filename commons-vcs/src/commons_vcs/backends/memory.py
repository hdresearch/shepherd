"""In-process backend — preserves Phase -1 behavior.

Object storage, refs, and inverse-edge index all live in plain dicts.
Used as the default for tests and short-lived workloads.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .._types import Object


class MemoryBackend:
    """Backend implementation backed by in-process dicts.

    Refs and the inverse-edge index are kept as separate dicts to mirror
    the GitBackend's structural choices (refs are independently
    addressable; the index is keyed by (target, role) and stores sorted
    citer lists).
    """

    def __init__(self) -> None:
        self._objects: dict[str, Object] = {}
        self._refs: dict[str, str] = {}
        # (target, role) -> sorted list of citer digests
        self._index: dict[tuple[str, str], list[str]] = {}

    # --- Object storage ---

    def has_object(self, digest: str) -> bool:
        return digest in self._objects

    def read_object(self, digest: str) -> Object | None:
        obj = self._objects.get(digest)
        if obj is not None and obj.id != digest:
            raise ValueError(f"object store integrity failure: requested {digest}, stored {obj.id}")
        return obj

    def write_object(self, obj: Object) -> str:
        d = obj.id
        if d in self._objects:
            return d
        self._objects[d] = obj
        for e in obj.edges:
            key = (e.target, e.role)
            citers = self._index.setdefault(key, [])
            if d not in citers:
                citers.append(d)
                citers.sort()
        return d

    def iter_objects(self) -> Iterator[tuple[str, Object]]:
        return iter(self._objects.items())

    # --- Refs ---

    def set_ref(self, name: str, value: str) -> None:
        self._refs[name] = value

    def get_ref(self, name: str) -> str | None:
        return self._refs.get(name)

    def list_refs(self, prefix: str = "") -> Iterator[str]:
        for name in sorted(self._refs):
            if name.startswith(prefix):
                yield name

    def compare_and_swap_ref(self, name: str, expected: str | None, new: str) -> bool:
        current = self._refs.get(name)
        if current != expected:
            return False
        self._refs[name] = new
        return True

    def compare_and_delete_ref(self, name: str, expected: str | None) -> bool:
        current = self._refs.get(name)
        if current != expected:
            return False
        self._refs.pop(name, None)
        return True

    def delete_ref(self, name: str) -> None:
        self._refs.pop(name, None)

    # --- Inverse-edge index ---

    def cited_by(self, target: str, role: str) -> list[str]:
        return list(self._index.get((target, role), ()))

    def reindex(self) -> None:
        self._index = {}
        for d, obj in self._objects.items():
            for e in obj.edges:
                key = (e.target, e.role)
                citers = self._index.setdefault(key, [])
                if d not in citers:
                    citers.append(d)
        for citers in self._index.values():
            citers.sort()
