"""Universal kernel — Repo, Resolver, Profile.

A repository where every transition between states is itself a
content-addressed object checked into the repo. State, cause, and witness
are all instances of one universal Object type. The kernel manages
identity, typed edges, and verification; profiles supply schemas and
meaning.

The central structural choice is that edges are typed. An edge has a
`role` (a profile-defined string: "prior", "witness", "cause", "evidence",
…) and a `target` (the digest of another object). The kernel does not
interpret roles; it only knows that two edges with different roles are
structurally distinct. Roles are first-class data the kernel uses for
identity and verification dispatch; role semantics are profile-defined.

Object storage, refs, and the inverse-edge index are owned by a
pluggable Backend (see `commons_vcs.backends`). Repo is the policy
layer (validation, traversal, profile dispatch); Backend is the
storage layer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

# Re-export pure data types so callers can keep importing from
# `commons_vcs.kernel`.
from ._types import (
    Edge,
    Failure,
    FailureRecord,
    Object,
    Outcome,
    VerifyResult,
)
from .backends import Backend, MemoryBackend


@dataclass(frozen=True)
class Resolver:
    """Bound to one Object during validation.

    Profiles use a Resolver to look up parent objects by digest or by
    role, and to query inverse edges via `by_role_at`. The kernel
    constructs a Resolver per validation call; profiles never
    instantiate it themselves.
    """

    obj: Object
    _backend: Backend

    def by_digest(self, d: str) -> Object | None:
        """Return the object with this digest, or None if not in the repo."""
        return self._backend.read_object(d)

    def by_role(self, role: str) -> list[Object]:
        """Return all parent objects reached by edges of this role."""
        return [
            p for p in (self._backend.read_object(e.target) for e in self.obj.edges if e.role == role) if p is not None
        ]

    def edges_with(self, role: str) -> list[Edge]:
        """Return all edges of this role on the bound object."""
        return [e for e in self.obj.edges if e.role == role]

    def by_role_at(self, role: str, target: str) -> list[Object]:
        """Return Objects (other than self) with an edge `(role, target)`.

        This is the kernel's inverse-edge query — given a target digest,
        which sources cite it under this role? Profiles use it for
        policies that need to look across the repo, not just up one
        Object's own edges. Common uses:

        - Affinity prevention: at append time, reject a new Object that
          would compete with an existing one. (sgc V3 §5: at most one
          authorized successor per predecessor.)
        - Citation auditing: count how many proofs cite this rule, or
          how many bundles contain this evidence record.
        - Duplicate detection: confirm a witness or attestation hasn't
          been silently re-stamped.

        Self-exclusion means the Resolver's own bound Object is never
        in the result. At append time the new Object isn't in the store
        yet, so this is a no-op; during re-validation walks it matters.

        Implementation goes through the backend's inverse-edge index
        (O(1) lookup) rather than scanning all objects.
        """
        self_id = self.obj.id
        results: list[Object] = []
        for d in self._backend.cited_by(target, role):
            if d == self_id:
                continue
            obj = self._backend.read_object(d)
            if obj is not None:
                results.append(obj)
        return results

    def from_object(self, other: Object) -> Resolver:
        """Return a Resolver bound to a different Object in the same store."""
        return Resolver(obj=other, _backend=self._backend)


Validator = Callable[[Object, Resolver], Failure | None]
"""A profile validator returns None on success or a Failure on rejection.

`reason_kind` is profile-defined; profiles emitting plain schema
violations conventionally use `Failure("schema", ...)`. Validators that
want a downstream reliance mapper to distinguish their failure mode
from generic schema rejection (e.g., `missing_target`, `affinity`)
return that reason_kind directly.
"""


@dataclass(frozen=True)
class Profile:
    """A schema dispatch table — maps schema_ref strings to validators.

    A profile is the unit of meaning above the kernel: it declares which
    schemas it owns and how to validate objects of those schemas.
    """

    name: str
    validators: Mapping[str, Validator] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TypeError("Profile.name must be a non-empty string")
        object.__setattr__(self, "validators", MappingProxyType(dict(self.validators)))

    def owns(self, schema_ref: str) -> bool:
        """Return True if this profile has a validator for the given schema."""
        return schema_ref in self.validators


@dataclass(frozen=True)
class Repo:
    """Content-addressed object store with profile-driven verification.

    Operations:
        append(obj)                   validate, store, return digest
        get(d)                        fetch by digest; None if missing
        cited_by(target, role)        inverse-edge lookup
        verify(head, root, walk=...)  walk head→root via edges, validating

    Repo is the policy layer above a Backend. The default backend is
    MemoryBackend (in-process dicts); GitBackend stores via a real
    .git/ directory.
    """

    profiles: Sequence[Profile] = field(default_factory=tuple)
    backend: Backend = field(default_factory=MemoryBackend)

    def __post_init__(self) -> None:
        object.__setattr__(self, "profiles", tuple(self.profiles))
        self._validate_schema_ownership()

    def _validate_schema_ownership(self) -> None:
        owners: dict[str, str] = {}
        for profile in self.profiles:
            for schema_ref in profile.validators:
                owner = owners.get(schema_ref)
                if owner is not None:
                    raise ValueError(
                        f"schema {schema_ref!r} is owned by multiple profiles: {owner!r} and {profile.name!r}"
                    )
                owners[schema_ref] = profile.name

    def _validator(self, schema_ref: str) -> Validator | None:
        """Return the owning profile's validator for this schema, or None."""
        self._validate_schema_ownership()
        for p in self.profiles:
            if p.owns(schema_ref):
                return p.validators[schema_ref]
        return None

    def append(self, obj: Object) -> str:
        """Validate and store an object. Returns its digest.

        Raises ValueError if no profile owns the schema, an edge target
        is missing, or the profile validator rejects.
        """
        v = self._validator(obj.schema_ref)
        if v is None:
            raise ValueError(f"no profile owns schema {obj.schema_ref!r}")
        for e in obj.edges:
            if not self.backend.has_object(e.target):
                raise ValueError(f"missing edge target {e.target} (role={e.role!r})")
        result = v(obj, Resolver(obj=obj, _backend=self.backend))
        if result is not None:
            raise ValueError(f"profile rejected object: {result.reason_kind}: {result.reason}")
        return self.backend.write_object(obj)

    def get(self, d: str) -> Object | None:
        """Return the object with this digest, or None if not in the repo."""
        return self.backend.read_object(d)

    def cited_by(self, target: str, role: str) -> list[str]:
        """Return digests of Objects citing `target` via an edge of `role`.

        Sorted by digest for determinism. Used by meta-agent traversal
        and by Resolver.by_role_at internally.
        """
        return self.backend.cited_by(target, role)

    def verify(
        self,
        head: str,
        trust_root: str,
        *,
        walk: set[str] | None = None,
        validate_trust_root: bool = False,
    ) -> VerifyResult:
        """Walk edges from head to trust_root, validating every visited object.

        walk: if provided, only follow edges whose role is in this set.
              If None (default), follow all edges. This lets a profile
              with multiple edge classes (e.g., "witness" and "cause")
              ask focused questions:
                - walk={"cause"}    re-derive state via projection
                - walk={"witness"}  audit production environment
                - walk=None         everything

        Outcomes (see Outcome's Literal members):
            ok.verified            head reachable, all visited objects valid
            unknown.incomplete     a required object is missing from this view
                                   (kernel-frontier-detected; early return)
            fail.unreachable       head not reachable from trust_root via the
                                   selected edge classes
            fail.invalid_object    one or more visited objects failed
                                   profile validation. Returned along with
                                   structured FailureRecord entries.

        Returns a VerifyResult NamedTuple with .outcome, .verified,
        .missing, .failures.

        Collect-all semantics: a validator failure does NOT short-circuit
        the walk. Every visited object is validated; every failure is
        collected as a FailureRecord.
        """
        if not self.backend.has_object(head) and (head != trust_root or validate_trust_root):
            return VerifyResult(
                outcome="unknown.incomplete",
                verified=[],
                missing=[head],
                failures=[],
            )
        verified: list[str] = []
        failures: list[FailureRecord] = []
        seen: set[str] = set()
        frontier: list[str] = [head]
        reached = head == trust_root
        while frontier:
            d = frontier.pop()
            if d in seen:
                continue
            seen.add(d)
            if d == trust_root:
                reached = True
                if not validate_trust_root:
                    continue
            obj = self.backend.read_object(d)
            if obj is None:
                # Kernel-frontier-missing: early return. The verifier
                # cannot make a coherent judgment without the object's
                # canonical bytes; collecting more failures past this
                # point would not be reliable.
                return VerifyResult(
                    outcome="unknown.incomplete",
                    verified=verified,
                    missing=[d],
                    failures=failures,
                )
            v = self._validator(obj.schema_ref)
            if v is None:
                failures.append(
                    FailureRecord(
                        digest=d,
                        schema_ref=obj.schema_ref,
                        reason_kind="unowned_schema",
                        reason=f"no profile owns schema {obj.schema_ref!r}",
                    )
                )
            else:
                result = v(obj, Resolver(obj=obj, _backend=self.backend))
                if result is None:
                    verified.append(d)
                else:
                    failures.append(
                        FailureRecord(
                            digest=d,
                            schema_ref=obj.schema_ref,
                            reason_kind=result.reason_kind,
                            reason=result.reason,
                        )
                    )
            if d == trust_root:
                continue
            for e in obj.edges:
                if walk is None or e.role in walk:
                    frontier.append(e.target)
        if failures:
            outcome: Outcome = "fail.invalid_object"
        elif not reached:
            outcome = "fail.unreachable"
        else:
            outcome = "ok.verified"
        return VerifyResult(
            outcome=outcome,
            verified=verified,
            missing=[],
            failures=failures,
        )
