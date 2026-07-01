"""Backend protocol — what Repo expects from its storage layer.

A Backend owns three concerns:

1. Object storage. Content-addressed: write_object(obj) is keyed by
   the object's commons digest; read_object(digest) returns the
   Object or None.

2. Named refs. Refs are short string names mapped to string values
   (typically digests, but the backend does not interpret them).
   compare_and_swap_ref is the atomicity primitive.

3. Inverse-edge index. cited_by(target, role) returns the digests of
   objects whose edges point at target with this role. The index is
   maintained on every write_object; backends choose how to store it.

The protocol is deliberately minimal. Higher-level concerns
(validation, traversal, profile dispatch) live in Repo, not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .._types import Object


@runtime_checkable
class Backend(Protocol):
    # --- Object storage ---

    def has_object(self, digest: str) -> bool:
        """Return True iff an object with this digest is stored."""
        ...

    def read_object(self, digest: str) -> Object | None:
        """Return the object with this digest, or None if absent."""
        ...

    def write_object(self, obj: Object) -> str:
        """Store obj and update the inverse-edge index. Returns obj.id.

        Idempotent: re-writing an already-stored object is a no-op
        (does not duplicate index entries).
        """
        ...

    def iter_objects(self) -> Iterator[tuple[str, Object]]:
        """Yield (digest, Object) for every stored object.

        Order is unspecified. Used for full-graph operations like
        reindex; not on hot paths.
        """
        ...

    # --- Refs ---

    def set_ref(self, name: str, value: str) -> None:
        """Set ref `name` to `value`, overwriting any prior value."""
        ...

    def get_ref(self, name: str) -> str | None:
        """Return the value of ref `name`, or None if unset."""
        ...

    def list_refs(self, prefix: str = "") -> Iterator[str]:
        """Yield every ref name starting with `prefix`."""
        ...

    def compare_and_swap_ref(self, name: str, expected: str | None, new: str) -> bool:
        """Atomically set `name` to `new` iff its current value equals `expected`.

        `expected=None` means "ref is unset"; the swap creates the ref.
        Returns True on success, False if the current value did not match
        (no state change).
        """
        ...

    def compare_and_delete_ref(self, name: str, expected: str | None) -> bool:
        """Atomically delete `name` iff its current value equals `expected`.

        `expected=None` means "ref is unset"; deleting an already-unset ref
        succeeds, but an existing ref with any value does not match. Returns
        True on success, False if the current value did not match.
        """
        ...

    def delete_ref(self, name: str) -> None:
        """Remove ref `name`. No-op if the ref is already absent."""
        ...

    # --- Inverse-edge index ---

    def cited_by(self, target: str, role: str) -> list[str]:
        """Return digests of objects citing `target` via an edge of `role`.

        Sorted by digest for determinism. Empty list if no citers.
        """
        ...

    def reindex(self) -> None:
        """Rebuild the inverse-edge index from stored objects.

        Idempotent. Used after corruption recovery or import.
        """
        ...
