"""Backend implementations for commons-vcs storage.

A Backend owns object storage, named refs, and the inverse-edge index.
Repo is the thin policy layer above (validation, traversal, profile
dispatch); Backend is the storage layer below.

Two implementations:

    MemoryBackend
        In-process dicts. The default; preserves Phase -1 behavior.

    GitBackend (Spike 1+)
        pygit2 over a real .git/ directory. Objects stored as Git blobs;
        refs under refs/commons-vcs/*; inverse-edge index as
        sidecar refs+blobs.

Backends are interchangeable from Repo's perspective; the choice is a
deployment-time configuration, not a code change.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._protocol import Backend
from .memory import MemoryBackend

if TYPE_CHECKING:
    from .git import GitBackend


def _git_backend() -> type[GitBackend]:  # lazy import so pygit2 isn't required for memory-only use
    from .git import GitBackend

    return GitBackend


__all__ = ["Backend", "MemoryBackend", "_git_backend"]
