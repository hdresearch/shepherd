"""Typed narrowing helpers for repeated pygit2 access patterns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pygit2

if TYPE_CHECKING:
    from collections.abc import Iterable


def topological_commits(
    repo: pygit2.Repository,
    start: pygit2.Oid | str,
) -> Iterable[pygit2.Commit]:
    """Return a topological walker with a concrete commit element type."""
    return cast(
        "Iterable[pygit2.Commit]",
        repo.walk(start, cast("Any", pygit2.GIT_SORT_TOPOLOGICAL)),
    )


def require_object(repo: pygit2.Repository, oid: pygit2.Oid) -> pygit2.Object:
    """Load one object or raise when the OID is missing."""
    obj = repo.get(oid)
    if obj is None:
        raise KeyError(str(oid))
    return obj


def require_commit(repo: pygit2.Repository, oid: pygit2.Oid, *, context: str) -> pygit2.Commit:
    obj = require_object(repo, oid)
    if isinstance(obj, pygit2.Commit):
        return obj
    raise TypeError(f"Expected Commit for {context}, got {type(obj).__name__}.")


def require_tree(repo: pygit2.Repository, oid: pygit2.Oid, *, context: str) -> pygit2.Tree:
    obj = require_object(repo, oid)
    if isinstance(obj, pygit2.Tree):
        return obj
    raise TypeError(f"Expected Tree for {context}, got {type(obj).__name__}.")


def require_blob(repo: pygit2.Repository, oid: pygit2.Oid, *, context: str) -> pygit2.Blob:
    obj = require_object(repo, oid)
    if isinstance(obj, pygit2.Blob):
        return obj
    raise TypeError(f"Expected Blob for {context}, got {type(obj).__name__}.")


def tree_entry(tree: pygit2.Tree, name: str) -> Any | None:
    """Return one tree entry or None when the name is absent."""
    try:
        return cast("Any", tree[name])
    except KeyError:
        return None


def tree_entry_id(tree: pygit2.Tree, name: str) -> pygit2.Oid | None:
    """Return the child object id for one tree entry when present."""
    entry = tree_entry(tree, name)
    if entry is None:
        return None
    return cast("pygit2.Oid", entry.id)


def tree_entry_filemode(tree: pygit2.Tree, name: str) -> int | None:
    """Return the filemode for one tree entry when present."""
    entry = tree_entry(tree, name)
    if entry is None:
        return None
    return cast("int", entry.filemode)


def sorted_tree_entries(tree: pygit2.Tree) -> list[Any]:
    """Return tree entries sorted by name with one localized cast seam."""
    return sorted(cast("Iterable[Any]", tree), key=lambda entry: str(entry.name))


def lookup_path(repo: pygit2.Repository, root_tree: pygit2.Tree, path: str) -> pygit2.Object | None:
    """Resolve a slash-delimited path from one root tree."""
    current: pygit2.Object = root_tree
    if not path:
        return current
    for segment in path.split("/"):
        if not isinstance(current, pygit2.Tree):
            return None
        child_oid = tree_entry_id(current, segment)
        if child_oid is None:
            return None
        try:
            current = require_object(repo, child_oid)
        except KeyError:
            return None
    return current
