"""GitBackend — pygit2 over a real .git/ directory.

Layout under .git/:

    objects/                            (Git object store, shared)
    refs/commons-vcs/
        objects/sha256/<hex>            ref -> blob (canonical bytes of Object)
        refs/<name>                     ref -> blob (UTF-8 bytes of set_ref value)
        index/sha256/<target>/<role>    ref -> blob (newline-separated citers)
        pins/<name>                        ref -> Git OID (caller-managed pins)

Why each piece:

- Sharing .git/objects/ with the underlying Git repo lets normal Git
  tooling (`git bundle`, `git fsck`, `git gc`) operate over the whole
  graph in one shot, given the pin-ref discipline (refactor.md §9.2.1).

- Named refs (set_ref API) point indirectly at value blobs so refs
  can carry arbitrary string values — including commons digests, which
  contain `:` and aren't valid as direct Git OID-like ref targets.
  The indirection trades one blob per ref update for protocol
  uniformity with MemoryBackend.

- Pin refs point directly at caller-selected Git OIDs because Git's
  reachability traversal ignores arbitrary strings inside blobs. Pin refs
  are explicit backend operations; profiles/coordinators decide what to pin.

- Compare-and-swap shells out to `git update-ref <name> <new> <expected>`
  because pygit2 has no built-in expected-OID gate. Git's update-ref
  is atomic at the filesystem layer (lockfile + atomic rename).

Ref-name encoding: commons digests contain `:` which is forbidden in
Git ref names. We map digest `sha256:abc...` to ref segment
`sha256/abc...` and back. The mapping is one-to-one.
"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pygit2

from .._types import Edge, Object
from ..canonical import canonical_value_from_bytes

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

# Ref namespaces under refs/commons-vcs/
NS_OBJECTS = "refs/commons-vcs/objects/"
NS_REFS = "refs/commons-vcs/refs/"
NS_INDEX = "refs/commons-vcs/index/"
NS_PINS = "refs/commons-vcs/pins/"


class ConcurrentIndexUpdateError(RuntimeError):
    """Raised when the inverse-edge index loses too many CAS races."""


class ConcurrentObjectWriteError(RuntimeError):
    """Raised when an object ref cannot be installed after CAS retries."""


class RefInstallError(RuntimeError):
    """Raised when an immutable backend ref cannot be installed."""


class RefTransactionError(RuntimeError):
    """Raised when a Git ref transaction cannot be executed."""


class ScopeLockTimeoutError(TimeoutError):
    """Raised when a per-scope advisory lock cannot be acquired in time."""


@dataclass(frozen=True)
class GitRefChange:
    """One low-level Git ref change for `GitBackend.update_refs_atomically`."""

    ref_name: str
    new_oid: str | None
    expected_oid: str | None
    check_expected: bool = True


def _digest_to_segment(digest: str) -> str:
    """Map 'sha256:abc...' -> 'sha256/abc...'.

    Git refs forbid `:`. The substitution is one-to-one within the
    expected digest format; we don't need a more general escape
    because algorithm prefixes are short, lowercase, and free of
    other reserved characters.
    """
    if ":" not in digest:
        raise ValueError(f"digest missing algorithm prefix: {digest!r}")
    algo, hex_part = digest.split(":", 1)
    return f"{algo}/{hex_part}"


def _segment_to_digest(segment: str) -> str:
    """Inverse of _digest_to_segment."""
    if "/" not in segment:
        raise ValueError(f"ref segment missing algorithm: {segment!r}")
    algo, hex_part = segment.split("/", 1)
    return f"{algo}:{hex_part}"


def _role_to_segment(role: str) -> str:
    """Encode an arbitrary edge role as one safe Git ref path segment."""
    return "utf8hex-" + role.encode("utf-8").hex()


def _decode_object(canonical_bytes: bytes) -> Object:
    """Reconstruct an Object from its canonical bytes."""
    parsed = canonical_value_from_bytes(canonical_bytes)
    if not isinstance(parsed, dict):
        raise TypeError("expected canonical object payload")
    if parsed.get("kind") != "object":
        raise ValueError(f"expected kind=object, got {parsed.get('kind')!r}")
    edges = tuple(Edge(role=e["role"], target=e["target"]) for e in parsed.get("edges", []))
    return Object(
        schema_ref=parsed["schema_ref"],
        body=parsed["body"],
        edges=edges,
    )


class GitBackend:
    """Backend implementation backed by a real .git/ directory.

    Construct via `GitBackend.init(path)` to create a fresh repo, or
    `GitBackend.open(path)` to attach to an existing one. The backend
    works against bare or non-bare Git repositories interchangeably;
    pygit2 abstracts that away.
    """

    def __init__(self, repo: pygit2.Repository) -> None:
        self._repo = repo
        self._git_dir = Path(repo.path).resolve()
        self._index_marker_blob_oid: str | None = None
        self._index_read_cache: dict[str, tuple[tuple[object, ...], list[str]]] = {}

    @classmethod
    def init(cls, path: Path | str, *, bare: bool = False) -> GitBackend:
        """Create a new Git repo at `path` and return a backend over it."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        repo = pygit2.init_repository(str(path), bare=bare)
        return cls(repo)

    @classmethod
    def open(cls, path: Path | str) -> GitBackend:
        """Open an existing Git repo at `path`."""
        return cls(pygit2.Repository(str(path)))

    # --- Internal helpers ---

    def _write_blob(self, payload: bytes) -> str:
        """Write a Git blob and return its OID hex."""
        oid = self._repo.create_blob(payload)
        return str(oid)

    def _read_blob(self, oid_hex: str) -> bytes:
        """Read a Git blob's contents."""
        obj = self._repo[oid_hex]
        if obj.type != pygit2.GIT_OBJECT_BLOB:
            raise ValueError(f"expected blob at {oid_hex}, got type {obj.type}")
        return cast("bytes", cast("Any", obj).data)

    def _ref_target_oid(self, ref_name: str) -> str | None:
        """Return the Git OID hex that the ref points at, or None."""
        try:
            ref = self._repo.references[ref_name]
        except KeyError:
            return None
        return str(ref.target)

    def _set_ref_to_oid(self, ref_name: str, oid_hex: str) -> None:
        """Create or overwrite a ref to point at a given Git OID. No CAS."""
        # pygit2's references.create has force=True which overwrites.
        self._repo.references.create(ref_name, oid_hex, force=True)

    def _delete_ref(self, ref_name: str) -> None:
        with suppress(KeyError):
            self._repo.references.delete(ref_name)

    def _update_ref_cas(
        self,
        ref_name: str,
        new_oid: str,
        expected_oid: str | None,
    ) -> bool:
        """Atomically update ref via `git update-ref`. CAS at the OID level.

        Returns True on success, False if expected didn't match.
        """
        cmd = ["git", "update-ref"]
        cmd.extend([ref_name, new_oid])
        # Empty string for expected means "must not exist" in update-ref's
        # CAS semantics.
        cmd.append(expected_oid if expected_oid is not None else "")
        result = subprocess.run(
            cmd,
            cwd=str(self._git_dir),
            capture_output=True,
            check=False,
            text=True,
        )
        return result.returncode == 0

    def update_refs_atomically(self, changes: Sequence[GitRefChange]) -> bool:
        """Apply low-level Git ref changes in one `git update-ref` transaction.

        The helper intentionally works at Git-ref/OID level. Callers that use
        commons string refs should prepare value blobs before constructing
        changes; callers that update product refs can pass their Git object OIDs
        directly.
        """
        if not changes:
            return True

        seen_refs: set[str] = set()
        lines = ["start"]
        for change in changes:
            if change.ref_name in seen_refs:
                raise RefTransactionError(f"duplicate ref in transaction: {change.ref_name!r}")
            seen_refs.add(change.ref_name)
            if change.new_oid is None:
                command = f"delete {change.ref_name}"
            else:
                command = f"update {change.ref_name} {change.new_oid}"
            if change.check_expected:
                command = f"{command} {change.expected_oid or pygit2.GIT_OID_HEX_ZERO}"
            lines.append(command)
        lines.extend(("prepare", "commit"))
        result = subprocess.run(
            ["git", "update-ref", "--stdin"],
            cwd=str(self._git_dir),
            input="\n".join(lines) + "\n",
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode == 0:
            return True
        if "cannot lock ref" in result.stderr or "reference already exists" in result.stderr:
            return False
        raise RefTransactionError(result.stderr.strip() or result.stdout.strip() or "git update-ref failed")

    def _delete_ref_cas(self, ref_name: str, expected_oid: str | None) -> bool:
        """Atomically delete a ref via `git update-ref -d`."""
        cmd = ["git", "update-ref", "-d", ref_name]
        if expected_oid is not None:
            cmd.append(expected_oid)
        result = subprocess.run(
            cmd,
            cwd=str(self._git_dir),
            capture_output=True,
            check=False,
            text=True,
        )
        return result.returncode == 0

    def _iter_ref_names(self, prefix: str) -> Iterator[str]:
        """Yield ref names with `prefix` without enumerating libgit2 refs.

        Marker-index reads are prefix queries. Iterating
        `self._repo.references` asks libgit2 for the whole ref namespace and
        dominates `cited_by` latency once every citer is its own marker ref.
        Walking the loose-ref subtree is the hot path; parsing packed-refs
        preserves correctness after Git compacts refs.
        """
        seen: set[str] = set()
        loose_root = self._git_dir / prefix
        if loose_root.is_file():
            seen.add(prefix)
            yield prefix
        elif loose_root.exists():
            for path in loose_root.rglob("*"):
                if not path.is_file() or path.name.endswith(".lock"):
                    continue
                ref_name = path.relative_to(self._git_dir).as_posix()
                if ref_name.startswith(prefix) and ref_name not in seen:
                    seen.add(ref_name)
                    yield ref_name

        packed_refs = self._git_dir / "packed-refs"
        if not packed_refs.exists():
            return
        for line in packed_refs.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            parts = line.split(" ", 1)
            if len(parts) != 2:
                continue
            ref_name = parts[1]
            if ref_name.startswith(prefix) and ref_name not in seen:
                seen.add(ref_name)
                yield ref_name

    def _scope_lock_path(self, scope_id: str) -> Path:
        return self._git_dir / "commons-vcs" / "locks" / "scope" / f"{_digest_to_segment(scope_id)}.lock"

    @contextmanager
    def scope_lock(self, scope_id: str, *, timeout: float = 30.0) -> Iterator[None]:
        """Hold the per-scope advisory lock for a multi-step emit cycle.

        The timeout only bounds acquisition. Once acquired, the caller may
        hold the lock for as long as the observed operation takes. If the
        process dies, the OS releases the flock with the file descriptor.
        """
        lock_path = self._scope_lock_path(scope_id)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o666)
        acquired = False
        deadline = time.monotonic() + timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ScopeLockTimeoutError(f"timed out acquiring scope lock for {scope_id}") from None
                    time.sleep(0.01)
            yield
        finally:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    # --- Object storage ---

    def _object_ref_name(self, digest: str) -> str:
        return NS_OBJECTS + _digest_to_segment(digest)

    def _ensure_object_ref(self, ref_name: str, blob_oid: str) -> None:
        try:
            self._ensure_immutable_ref(ref_name, blob_oid)
        except RefInstallError as exc:
            raise ConcurrentObjectWriteError(str(exc)) from exc

    def _ensure_immutable_ref(self, ref_name: str, oid_hex: str) -> None:
        """Install a deterministic immutable ref.

        Object refs and marker-index refs are content-derived. Concurrent
        writers racing on the same ref must all be trying to point it at the
        same OID. Create the ref with Git's expected-old-OID gate so a
        concurrent unexpected writer is observed as an invariant violation,
        not overwritten.
        """
        for attempt in range(16):
            current_oid = self._ref_target_oid(ref_name)
            if current_oid is not None:
                if current_oid != oid_hex:
                    raise RefInstallError(f"immutable ref {ref_name!r} points at unexpected object {current_oid}")
                return
            if self._update_ref_cas(ref_name, oid_hex, None):
                return
            current_oid = self._ref_target_oid(ref_name)
            if current_oid == oid_hex:
                return
            if current_oid is not None:
                raise RefInstallError(f"immutable ref {ref_name!r} points at unexpected object {current_oid}")
            time.sleep(min(0.001 * (2**attempt), 0.05))
        raise RefInstallError(f"failed to install immutable ref {ref_name!r}")

    def has_object(self, digest: str) -> bool:
        return self._ref_target_oid(self._object_ref_name(digest)) is not None

    def read_object(self, digest: str) -> Object | None:
        oid = self._ref_target_oid(self._object_ref_name(digest))
        if oid is None:
            return None
        canonical = self._read_blob(oid)
        obj = _decode_object(canonical)
        if obj.id != digest:
            raise ValueError(f"object ref integrity failure: requested {digest}, decoded {obj.id}")
        return obj

    def write_object(self, obj: Object) -> str:
        d = obj.id
        ref_name = self._object_ref_name(d)
        if self._ref_target_oid(ref_name) is None:
            # Write the canonical bytes as a blob, then CAS-create the
            # object ref. Concurrent writers of the same content race to
            # install the same blob OID; losers observe the installed ref
            # and continue to the idempotent derived-state repairs below.
            canonical = obj.canonical_bytes()
            blob_oid = self._write_blob(canonical)
            self._ensure_object_ref(ref_name, blob_oid)
        # Update inverse-edge index.
        for e in obj.edges:
            self._index_add(e.target, e.role, d)
        return d

    def iter_objects(self) -> Iterator[tuple[str, Object]]:
        for ref_name in self._iter_ref_names(NS_OBJECTS):
            segment = ref_name[len(NS_OBJECTS) :]
            try:
                digest = _segment_to_digest(segment)
            except ValueError:
                continue
            obj = self.read_object(digest)
            if obj is not None:
                yield (digest, obj)

    # --- Refs (set_ref API) ---

    def _ref_path(self, name: str) -> str:
        return NS_REFS + name

    def _read_value_blob(self, oid_hex: str) -> str:
        return self._read_blob(oid_hex).decode("utf-8")

    def prepare_ref_value(self, value: str) -> str:
        """Write a commons string-ref value blob and return its Git OID."""
        return self._write_blob(value.encode("utf-8"))

    def prepared_set_ref_change(
        self,
        name: str,
        value: str,
        *,
        expected: str | None,
    ) -> GitRefChange | None:
        """Prepare a guarded commons string-ref update for a Git transaction.

        Returns None when the current value does not equal `expected`.
        """
        ref_name = self._ref_path(name)
        current_oid = self._ref_target_oid(ref_name)
        current_value = None if current_oid is None else self._read_value_blob(current_oid)
        if current_value != expected:
            return None
        return GitRefChange(
            ref_name=ref_name,
            new_oid=self.prepare_ref_value(value),
            expected_oid=current_oid,
        )

    def prepared_delete_ref_change(self, name: str, *, expected: str | None) -> GitRefChange | None:
        """Prepare a guarded commons string-ref delete for a Git transaction."""
        ref_name = self._ref_path(name)
        current_oid = self._ref_target_oid(ref_name)
        current_value = None if current_oid is None else self._read_value_blob(current_oid)
        if current_value != expected:
            return None
        return GitRefChange(ref_name=ref_name, new_oid=None, expected_oid=current_oid)

    def set_ref(self, name: str, value: str) -> None:
        blob_oid = self._write_blob(value.encode("utf-8"))
        self._set_ref_to_oid(self._ref_path(name), blob_oid)

    def get_ref(self, name: str) -> str | None:
        oid = self._ref_target_oid(self._ref_path(name))
        if oid is None:
            return None
        return self._read_value_blob(oid)

    def list_refs(self, prefix: str = "") -> Iterator[str]:
        full_prefix = self._ref_path(prefix)
        for ref_name in sorted(self._iter_ref_names(full_prefix)):
            yield ref_name[len(NS_REFS) :]

    def compare_and_swap_ref(self, name: str, expected: str | None, new: str) -> bool:
        ref_name = self._ref_path(name)
        current_oid = self._ref_target_oid(ref_name)
        # Check expected matches current.
        if current_oid is None:
            current_value: str | None = None
        else:
            current_value = self._read_value_blob(current_oid)
        if current_value != expected:
            return False
        # Write new value blob, then CAS the ref to it.
        new_oid = self._write_blob(new.encode("utf-8"))
        return self._update_ref_cas(ref_name, new_oid, current_oid)

    def compare_and_delete_ref(self, name: str, expected: str | None) -> bool:
        ref_name = self._ref_path(name)
        current_oid = self._ref_target_oid(ref_name)
        if current_oid is None:
            return expected is None
        current_value = self._read_value_blob(current_oid)
        if current_value != expected:
            return False
        return self._delete_ref_cas(ref_name, current_oid)

    def delete_ref(self, name: str) -> None:
        self._delete_ref(self._ref_path(name))

    # --- Explicit Git object pins ---

    def _pin_ref_path(self, name: str) -> str:
        if not name:
            raise ValueError("pin name must be non-empty")
        if name.startswith("/") or name.endswith("/"):
            raise ValueError(f"invalid pin name: {name!r}")
        ref_name = NS_PINS + name
        result = subprocess.run(
            ["git", "check-ref-format", ref_name],
            cwd=str(self._git_dir),
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(f"invalid pin name: {name!r}")
        return ref_name

    def pin_git_object(self, name: str, oid_hex: str) -> None:
        """Pin a Git object under refs/commons-vcs/pins/<name>.

        The backend only manages reachability. Callers choose semantic pin
        names and decide which Git objects deserve retention.
        """
        if oid_hex not in self._repo:
            raise ValueError(f"cannot pin missing Git object {oid_hex!r}")
        self._set_ref_to_oid(self._pin_ref_path(name), oid_hex)

    def unpin_git_object(self, name: str) -> None:
        """Remove a caller-managed Git object pin if it exists."""
        self._delete_ref(self._pin_ref_path(name))

    # --- Inverse-edge index ---

    def _index_ref_name(self, target: str, role: str) -> str:
        return NS_INDEX + _digest_to_segment(target) + "/" + _role_to_segment(role) + "/"

    def _index_marker_ref_name(self, target: str, role: str, citer: str) -> str:
        return self._index_ref_name(target, role) + _digest_to_segment(citer)

    def _index_marker_oid(self) -> str:
        if self._index_marker_blob_oid is None:
            self._index_marker_blob_oid = self._write_blob(b"commons-vcs.index-marker.v1\n")
        return self._index_marker_blob_oid

    def _index_ref_stamp(self, ref_name: str) -> tuple[object, ...]:
        loose_root = self._git_dir / ref_name
        loose_stamp: object
        if loose_root.exists():
            child_stamps = tuple(
                sorted((child.name, child.stat().st_mtime_ns) for child in loose_root.iterdir() if child.is_dir())
            )
            loose_stamp = (loose_root.stat().st_mtime_ns, child_stamps)
        else:
            loose_stamp = None

        packed_refs = self._git_dir / "packed-refs"
        packed_stamp = packed_refs.stat().st_mtime_ns if packed_refs.exists() else None
        return (loose_stamp, packed_stamp)

    def _index_read_list(self, ref_name: str) -> list[str]:
        stamp = self._index_ref_stamp(ref_name)
        cached = self._index_read_cache.get(ref_name)
        if cached is not None and cached[0] == stamp:
            return list(cached[1])

        citers: set[str] = set()
        loose_root = self._git_dir / ref_name
        if loose_root.exists():
            for algo_dir in loose_root.iterdir():
                if not algo_dir.is_dir():
                    continue
                algo = algo_dir.name
                for marker in algo_dir.iterdir():
                    if marker.is_file() and not marker.name.endswith(".lock"):
                        citers.add(f"{algo}:{marker.name}")

        packed_refs = self._git_dir / "packed-refs"
        if packed_refs.exists():
            for line in packed_refs.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith(("#", "^")):
                    continue
                parts = line.split(" ", 1)
                if len(parts) != 2:
                    continue
                marker_ref = parts[1]
                if not marker_ref.startswith(ref_name):
                    continue
                try:
                    citers.add(_segment_to_digest(marker_ref[len(ref_name) :]))
                except ValueError:
                    continue
        result = sorted(citers)
        self._index_read_cache[ref_name] = (stamp, result)
        return list(result)

    def _index_write_list(self, ref_name: str, citers: list[str]) -> None:
        marker_oid = self._index_marker_oid()
        for citer in citers:
            self._ensure_immutable_ref(ref_name + _digest_to_segment(citer), marker_oid)

    def _index_read_oid_and_list(self, ref_name: str) -> tuple[str | None, list[str]]:
        oid = self._ref_target_oid(ref_name)
        if oid is None:
            return None, []
        payload = self._read_blob(oid).decode("utf-8")
        if not payload:
            return oid, []
        return oid, payload.split("\n")

    def _index_write_list_cas(
        self,
        ref_name: str,
        expected_oid: str | None,
        citers: list[str],
    ) -> bool:
        payload = "\n".join(citers)
        blob_oid = self._write_blob(payload.encode("utf-8"))
        return self._update_ref_cas(ref_name, blob_oid, expected_oid)

    def _index_add(self, target: str, role: str, citer: str) -> None:
        ref_name = self._index_ref_name(target, role)
        self._index_read_cache.pop(ref_name, None)
        marker_ref = self._index_marker_ref_name(target, role, citer)
        try:
            self._ensure_immutable_ref(marker_ref, self._index_marker_oid())
        except RefInstallError as exc:
            raise ConcurrentIndexUpdateError(
                f"failed to update inverse-edge index for target={target!r}, role={role!r}"
            ) from exc

    def cited_by(self, target: str, role: str) -> list[str]:
        return self._index_read_list(self._index_ref_name(target, role))

    def reindex(self) -> None:
        self._index_read_cache.clear()
        # Drop existing index refs.
        for ref_name in list(self._iter_ref_names(NS_INDEX)):
            self._delete_ref(ref_name)
        # Rebuild.
        accum: dict[str, list[str]] = {}
        for digest, obj in self.iter_objects():
            for e in obj.edges:
                key = self._index_ref_name(e.target, e.role)
                accum.setdefault(key, []).append(digest)
        for ref_name, citers in accum.items():
            citers.sort()
            self._index_write_list(ref_name, citers)
