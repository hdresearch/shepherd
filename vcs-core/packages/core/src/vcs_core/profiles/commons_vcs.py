"""commons-vcs profile for vcs-core recording Objects.

This module owns vcs-core's production schema validators. It deliberately
contains no coordinator or Store porting logic; those layers construct Objects
and call into commons-vcs after domain policy has already run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from commons_vcs import Failure, Object, Profile, Resolver

_HEX = frozenset("0123456789abcdef")
_COMMIT_BODY_KEYS = frozenset({"workspace_tree", "git_object_format"})
_EFFECT_BODY_KEYS = frozenset({"effect_type", "substrate", "payload", "workspace_changes"})
_SCOPE_BODY_KEYS = frozenset({"name", "world_id", "scope_instance_id"})
_WORKSPACE_CHANGE_KEYS = frozenset({"path", "status", "content_digest", "git_filemode"})
_WORKSPACE_STATUSES = frozenset({"added", "modified", "deleted"})
_FILEMODES = frozenset({"100644", "100755"})


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _git_oid(value: object, object_format: str | None = None) -> bool:
    if not isinstance(value, str):
        return False
    expected_lengths = {"sha1": 40, "sha256": 64}
    if object_format is not None:
        length = expected_lengths.get(object_format)
        return length is not None and len(value) == length and all(char in _HEX for char in value)
    return len(value) in expected_lengths.values() and all(char in _HEX for char in value)


def _sha256_digest(value: object) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "sha256:"
    hex_part = value.removeprefix(prefix)
    return value.startswith(prefix) and len(hex_part) == 64 and all(char in _HEX for char in hex_part)


def _body_keys_are(body: Mapping[str, object], allowed: frozenset[str], schema_ref: str) -> Failure | None:
    unknown = sorted(set(body) - allowed)
    if unknown:
        return Failure("schema", f"{schema_ref}.body has unknown fields: {', '.join(unknown)}")
    return None


def validate_effect_v1(obj: Object, _resolver: Resolver) -> Failure | None:
    """Validate a native vcs-core effect Object."""
    if obj.edges:
        return Failure("schema", "vcscore/effect/v1 does not allow edges")
    body = obj.body
    if failure := _body_keys_are(body, _EFFECT_BODY_KEYS, obj.schema_ref):
        return failure
    if not _non_empty_string(body.get("effect_type")):
        return Failure("schema", "vcscore/effect/v1.body.effect_type must be a non-empty string")
    if not _non_empty_string(body.get("substrate")):
        return Failure("schema", "vcscore/effect/v1.body.substrate must be a non-empty string")
    if not isinstance(body.get("payload"), Mapping):
        return Failure("schema", "vcscore/effect/v1.body.payload must be an object")
    if "workspace_changes" not in body:
        return None
    changes = body["workspace_changes"]
    if not isinstance(changes, Sequence) or isinstance(changes, str):
        return Failure("schema", "vcscore/effect/v1.body.workspace_changes must be an array")
    for index, change in enumerate(changes):
        if not isinstance(change, Mapping):
            return Failure("schema", f"workspace_changes[{index}] must be an object")
        if failure := _body_keys_are(change, _WORKSPACE_CHANGE_KEYS, f"workspace_changes[{index}]"):
            return failure
        if not _non_empty_string(change.get("path")):
            return Failure("schema", f"workspace_changes[{index}].path must be a non-empty string")
        status = change.get("status")
        if status not in _WORKSPACE_STATUSES:
            return Failure("schema", f"workspace_changes[{index}].status must be added, modified, or deleted")
        if "content_digest" in change:
            content_digest = change["content_digest"]
            if not _sha256_digest(content_digest):
                return Failure("schema", f"workspace_changes[{index}].content_digest must be a sha256 digest")
        if "git_filemode" in change:
            filemode = change["git_filemode"]
            if filemode not in _FILEMODES:
                return Failure("schema", f"workspace_changes[{index}].git_filemode must be 100644 or 100755")
        if status == "deleted":
            present = sorted({"content_digest", "git_filemode"} & set(change))
            if present:
                return Failure(
                    "schema", f"workspace_changes[{index}] deleted change has invalid fields: {', '.join(present)}"
                )
        else:
            for field in ("content_digest", "git_filemode"):
                if field not in change:
                    return Failure("schema", f"workspace_changes[{index}].{field} is required for {status} changes")
    return None


def validate_scope_v1(obj: Object, resolver: Resolver) -> Failure | None:
    """Validate an immutable vcs-core scope identity Object."""
    body = obj.body
    if failure := _body_keys_are(body, _SCOPE_BODY_KEYS, obj.schema_ref):
        return failure
    for field in sorted(_SCOPE_BODY_KEYS):
        if not _non_empty_string(body.get(field)):
            return Failure("schema", f"vcscore/scope/v1.body.{field} must be a non-empty string")
    parent_edges = [edge for edge in obj.edges if edge.role == "parent_scope"]
    if len(parent_edges) > 1:
        return Failure("schema", "vcscore/scope/v1 allows at most one parent_scope edge")
    unknown_roles = sorted({edge.role for edge in obj.edges} - {"parent_scope"})
    if unknown_roles:
        return Failure("schema", f"vcscore/scope/v1 has unknown edge roles: {', '.join(unknown_roles)}")
    if parent_edges:
        parent = resolver.by_digest(parent_edges[0].target)
        if parent is None or parent.schema_ref != "vcscore/scope/v1":
            return Failure("schema", "vcscore/scope/v1.parent_scope must target vcscore/scope/v1")
    return None


def validate_commit_v1(obj: Object, resolver: Resolver) -> Failure | None:
    """Validate a vcs-core workspace projection commit Object."""
    body = obj.body
    if failure := _body_keys_are(body, _COMMIT_BODY_KEYS, obj.schema_ref):
        return failure
    object_format = body.get("git_object_format")
    if object_format not in {"sha1", "sha256"}:
        return Failure("schema", "vcscore/commit/v1.body.git_object_format must be sha1 or sha256")
    if not _git_oid(body.get("workspace_tree"), str(object_format)):
        return Failure("schema", "vcscore/commit/v1.body.workspace_tree must match git_object_format")

    by_role = {role: [edge for edge in obj.edges if edge.role == role] for role in ("parent", "effect", "scope")}
    unknown_roles = sorted({edge.role for edge in obj.edges} - set(by_role))
    if unknown_roles:
        return Failure("schema", f"vcscore/commit/v1 has unknown edge roles: {', '.join(unknown_roles)}")
    if len(by_role["parent"]) > 1:
        return Failure("schema", "vcscore/commit/v1 allows at most one parent edge")
    if len(by_role["effect"]) != 1:
        return Failure("schema", "vcscore/commit/v1 requires exactly one effect edge")
    if len(by_role["scope"]) != 1:
        return Failure("schema", "vcscore/commit/v1 requires exactly one scope edge")

    parent_edges = by_role["parent"]
    if parent_edges:
        parent = resolver.by_digest(parent_edges[0].target)
        if parent is None or parent.schema_ref != "vcscore/commit/v1":
            return Failure("schema", "vcscore/commit/v1.parent must target vcscore/commit/v1")
    scope = resolver.by_digest(by_role["scope"][0].target)
    if scope is None or scope.schema_ref != "vcscore/scope/v1":
        return Failure("schema", "vcscore/commit/v1.scope must target vcscore/scope/v1")
    effect = resolver.by_digest(by_role["effect"][0].target)
    if effect is None:
        return Failure("schema", "vcscore/commit/v1.effect must target an existing Object")
    return None


profile = Profile(
    name="vcscore",
    validators={
        "vcscore/commit/v1": validate_commit_v1,
        "vcscore/effect/v1": validate_effect_v1,
        "vcscore/scope/v1": validate_scope_v1,
    },
)
