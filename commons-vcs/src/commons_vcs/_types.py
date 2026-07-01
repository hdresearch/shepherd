"""Pure data types used by both kernel.py and backends/.

Split out of kernel.py to break the import cycle: backends import
Object/Edge types; kernel imports Backend from backends.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, is_dataclass
from types import MappingProxyType
from typing import Any, Literal, NamedTuple, cast

from .canonical import canonical_bytes, digest

_SHA256_DIGEST_PREFIX = "sha256:"
_SHA256_HEX_LENGTH = 64
_LOWER_HEX = frozenset("0123456789abcdef")


class Failure(NamedTuple):
    """A structured validator failure (what a validator returns).

    `reason_kind` is profile-defined: a downstream verifier service uses
    it to map structural conditions to a domain-specific reliance
    vocabulary (e.g., sgc maps `affinity` to `frozen`, `missing_target`
    to `caution`, `schema` to `corrupt`). A small set of conventional
    reason kinds (`schema`, `missing_target`) is shared across profiles
    to keep mappers portable.

    `reason` is human-readable diagnostic text. Reliance mappers MUST NOT
    pattern-match on it; that's what `reason_kind` is for.

    Inside `Repo.verify()`, each Failure is wrapped into a `FailureRecord`
    that adds the kernel-populated `digest` and `schema_ref` of the
    rejected Object.
    """

    reason_kind: str
    reason: str


class FailureRecord(NamedTuple):
    """A validator failure as recorded by `Repo.verify()`.

    Wraps a `Failure` returned by a validator with the kernel-populated
    context (`digest`, `schema_ref`) that locates the failure within the
    walk. Reliance mappers consume `list[FailureRecord]`.
    """

    digest: str
    schema_ref: str
    reason_kind: str
    reason: str


Outcome = Literal[
    "ok.verified",
    "unknown.incomplete",
    "fail.unreachable",
    "fail.invalid_object",
]


class VerifyResult(NamedTuple):
    """The structured result of `Repo.verify()`.

    `outcome` summarizes the verdict; see Outcome's literal members for
    the closed set of values. `verified` lists Objects that validated
    cleanly. `missing` lists digests absent from the store mid-walk
    (only populated under `unknown.incomplete`). `failures` lists
    structured validator rejections collected during the walk under
    collect-all semantics.
    """

    outcome: Outcome
    verified: list[str]
    missing: list[str]
    failures: list[FailureRecord]


def _normalize_json_value(value: Any, *, path: str) -> Any:
    """Return a detached JSON-primitive value or raise for non-canonical input."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        raise TypeError(f"floats are forbidden in Object.body at {path}")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"Object.body object keys must be strings at {path}")
            result[key] = _normalize_json_value(child, path=f"{path}.{key}")
        return result
    if isinstance(value, list):
        return [_normalize_json_value(child, path=f"{path}[{index}]") for index, child in enumerate(value)]
    if isinstance(value, tuple):
        raise TypeError(f"tuples are not Object.body inputs at {path}; use lists")
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError(f"bytes are not Object.body inputs at {path}; use a schema-declared encoding")
    if isinstance(value, (set, frozenset)):
        raise TypeError(f"sets are not Object.body inputs at {path}; use a sorted list")
    if is_dataclass(value) and not isinstance(value, type):
        raise TypeError(f"dataclasses are not Object.body inputs at {path}; project to primitives first")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, memoryview)):
        raise TypeError(f"{type(value).__name__} is not an Object.body array at {path}; use list")
    raise TypeError(f"{type(value).__name__} is not an Object.body JSON input at {path}")


def _copy_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _copy_json_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_copy_json_value(child) for child in value]
    return value


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json_value(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json_value(child) for child in value)
    return value


@dataclass(frozen=True)
class Edge:
    """A typed reference from one object to another.

    `role` is a profile-defined string (e.g., "prior", "witness", "cause").
    The kernel does not interpret it; it only ensures that edges contribute
    deterministically to the source object's identity. Two edges with the
    same role and target are distinct positions in the edges tuple but are
    not deduplicated by the kernel; profiles that require deduplication
    enforce it in their validators.
    """

    role: str
    target: str  # digest

    def __post_init__(self) -> None:
        if not isinstance(self.role, str) or not self.role:
            raise TypeError("Edge.role must be a non-empty string")
        if not isinstance(self.target, str) or not self.target:
            raise TypeError("Edge.target must be a non-empty digest string")
        if not _is_sha256_digest(self.target):
            raise ValueError("Edge.target must be a sha256:<64 lowercase hex> digest")


@dataclass(frozen=True)
class Object:
    """A content-addressed node in the causally-closed DAG.

    The kernel does not interpret schema_ref, body, or edge roles. It
    binds identity, edge structure, and the availability rule for
    verification.

    Edges are ordered; reordering changes the digest. Whether a given
    edge sequence is *meaningfully* ordered (citation order matters) or
    *operationally* ordered (the schema sorts edges before producing the
    canonical input) is a profile-level discipline.
    """

    schema_ref: str
    body: Mapping[str, Any]
    edges: tuple[Edge, ...] = ()
    _canonical_core: dict[str, Any] = field(init=False, repr=False)
    _id: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.schema_ref, str) or not self.schema_ref:
            raise TypeError("Object.schema_ref must be a non-empty string")
        edges = _normalize_edges(self.edges)
        body_copy = _normalize_json_value(self.body, path="body")
        if not isinstance(body_copy, dict):
            raise TypeError("Object.body must be a JSON object")
        canonical_core = {
            "kind": "object",
            "edges": [{"role": e.role, "target": e.target} for e in edges],
            "schema_ref": self.schema_ref,
            "body": body_copy,
        }
        d = digest(canonical_core)
        object.__setattr__(self, "body", _freeze_json_value(body_copy))
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "_canonical_core", canonical_core)
        object.__setattr__(self, "_id", d)

    @property
    def id(self) -> str:
        """The content-addressed digest of this object's canonical form."""
        return self._id

    def canonical_core(self) -> dict[str, Any]:
        """Return a detached JSON-primitive core for this Object's identity."""
        return cast("dict[str, Any]", _copy_json_value(self._canonical_core))

    def canonical_bytes(self) -> bytes:
        """Return the exact canonical bytes whose digest is this Object's id."""
        return canonical_bytes(self._canonical_core)


def _is_sha256_digest(value: str) -> bool:
    if not value.startswith(_SHA256_DIGEST_PREFIX):
        return False
    hex_part = value[len(_SHA256_DIGEST_PREFIX) :]
    return len(hex_part) == _SHA256_HEX_LENGTH and all(char in _LOWER_HEX for char in hex_part)


def _normalize_edges(edges: object) -> tuple[Edge, ...]:
    if isinstance(edges, tuple):
        normalized = edges
    elif isinstance(edges, list):
        normalized = tuple(edges)
    else:
        raise TypeError("Object.edges must be a tuple or list of Edge values")
    for index, edge in enumerate(normalized):
        if not isinstance(edge, Edge):
            raise TypeError(f"Object.edges[{index}] must be an Edge")
    return normalized
