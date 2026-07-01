"""File delta representation for SimpleWorkspace.

This module provides FileDelta and FileChangeset for representing
file changes with full content for effect replay.

Encoding strategy (validated by SW-02, SW-03 spikes):
- Text files: diff-match-patch delta (90-99% size reduction)
- Binary files: adaptive zlib compression + base64
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:
    from shepherd_contexts.simple_workspace.encoding import ContentEncoder


class FileDelta(BaseModel):
    """Change to a single file.

    Contains full information needed to apply the change,
    enabling state reconstruction from effects alone.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    operation: Literal["create", "modify", "delete"]

    # Content encoding
    encoding: Literal["full", "delta", "zlib_base64", "raw_base64"] = "full"
    content: bytes | None = None  # Encoded content

    # Metadata for verification
    new_content_hash: str | None = None
    old_content_hash: str | None = None  # For delta application verification
    new_size_bytes: int | None = None
    new_mode: int | None = None
    is_binary: bool = False

    @classmethod
    def create(
        cls,
        path: str,
        content: bytes,
        mode: int = 0o644,
        encoder: ContentEncoder | None = None,
    ) -> FileDelta:
        """Create a new file delta.

        Args:
            path: Relative file path
            content: File content bytes
            mode: File permissions
            encoder: Optional ContentEncoder for compression
        """
        if encoder is None:
            from shepherd_contexts.simple_workspace.encoding import get_encoder

            encoder = get_encoder()

        is_binary = encoder.is_binary(content)
        encoding, encoded = encoder.encode_new(content, is_binary)

        return cls(
            path=path,
            operation="create",
            encoding=encoding,
            content=encoded,
            new_content_hash=hashlib.sha256(content).hexdigest(),
            new_size_bytes=len(content),
            new_mode=mode,
            is_binary=is_binary,
        )

    @classmethod
    def modify(
        cls,
        path: str,
        old_content: bytes,
        new_content: bytes,
        encoder: ContentEncoder | None = None,
    ) -> FileDelta:
        """Create a modification delta.

        Args:
            path: Relative file path
            old_content: Original file content
            new_content: New file content
            encoder: Optional ContentEncoder for delta encoding
        """
        if encoder is None:
            from shepherd_contexts.simple_workspace.encoding import get_encoder

            encoder = get_encoder()

        is_binary = encoder.is_binary(new_content)
        encoding, encoded = encoder.encode_modification(old_content, new_content, is_binary)

        return cls(
            path=path,
            operation="modify",
            encoding=encoding,
            content=encoded,
            new_content_hash=hashlib.sha256(new_content).hexdigest(),
            old_content_hash=hashlib.sha256(old_content).hexdigest(),
            new_size_bytes=len(new_content),
            is_binary=is_binary,
        )

    @classmethod
    def delete(cls, path: str, old_hash: str | None = None) -> FileDelta:
        """Create a deletion delta.

        Args:
            path: Relative file path
            old_hash: Optional hash of deleted content for verification
        """
        return cls(
            path=path,
            operation="delete",
            old_content_hash=old_hash,
        )

    def decode_content(
        self,
        old_content: bytes | None = None,
        encoder: ContentEncoder | None = None,
    ) -> bytes | None:
        """Decode content back to original bytes.

        Args:
            old_content: Required for delta-encoded modifications
            encoder: Optional ContentEncoder

        Returns:
            Decoded content bytes, or None for deletions
        """
        if self.operation == "delete" or self.content is None:
            return None

        if encoder is None:
            from shepherd_contexts.simple_workspace.encoding import get_encoder

            encoder = get_encoder()

        return encoder.decode(self.encoding, self.content, old_content)


class FileChangeset(BaseModel):
    """Collection of file deltas from a single execution step.

    This is the SimpleWorkspace equivalent of a git patch -
    a set of changes that can be applied atomically.
    """

    model_config = ConfigDict(frozen=True)

    deltas: tuple[FileDelta, ...]
    source_step: str | None = None
    created_at: datetime | None = None  # Defaults in validator
    sha256: str | None = None  # Auto-computed for non-empty changesets

    @model_validator(mode="after")
    def _compute_sha256_and_defaults(self) -> Self:
        """Auto-compute sha256 for non-empty changesets."""
        # Set default created_at
        if self.created_at is None:
            object.__setattr__(self, "created_at", datetime.now(tz=UTC))

        # Compute sha256 for non-empty changesets (sorted by path)
        if self.deltas and not self.sha256:
            delta_parts = []
            for delta in sorted(self.deltas, key=lambda d: d.path):
                if delta.new_content_hash:
                    content_repr = delta.new_content_hash
                elif delta.new_size_bytes is not None:
                    content_repr = f"size:{delta.new_size_bytes}"
                else:
                    content_repr = ""  # Delete operations

                parts = [
                    delta.path,
                    delta.operation,
                    content_repr,
                    delta.old_content_hash or "",
                    str(delta.new_mode) if delta.new_mode is not None else "",
                ]
                delta_parts.append("|".join(parts))

            combined = "\n".join(delta_parts)
            computed = hashlib.sha256(combined.encode("utf-8")).hexdigest()
            object.__setattr__(self, "sha256", computed)

        return self

    @property
    def files_changed(self) -> tuple[str, ...]:
        """All file paths in changeset."""
        return tuple(d.path for d in self.deltas)

    @property
    def is_empty(self) -> bool:
        """Whether changeset has no changes."""
        return len(self.deltas) == 0

    @property
    def total_size_bytes(self) -> int:
        """Total encoded size of all deltas."""
        return sum(len(d.content) if d.content else 0 for d in self.deltas)

    def created_files(self) -> tuple[str, ...]:
        """Paths of created files."""
        return tuple(d.path for d in self.deltas if d.operation == "create")

    def modified_files(self) -> tuple[str, ...]:
        """Paths of modified files."""
        return tuple(d.path for d in self.deltas if d.operation == "modify")

    def deleted_files(self) -> tuple[str, ...]:
        """Paths of deleted files."""
        return tuple(d.path for d in self.deltas if d.operation == "delete")

    def __len__(self) -> int:
        """Number of deltas in changeset."""
        return len(self.deltas)
