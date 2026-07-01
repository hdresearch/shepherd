"""shepherd/effect/v1 profile (Phase -1 spike).

Validator enforces structural-shape only. Required body fields are the
stable provider-agnostic subset identified in the deep-dive plus
`scope_id` (recorded as a Phase -1 finding for shepherd — not on
ToolCallStarted today; see preflight/worked-example.md S3).

Edges: optionally an `executed-against` edge pointing at the prior
state (a `vcscore/commit/v1`). Cross-profile target — not constrained
at this layer.
"""

from __future__ import annotations

from commons_vcs import Failure, Object, Profile, Resolver

# Stable subset per preflight/worked-example.md and the shepherd deep-dive.
# `output` and `output_digest` are both required (Option C: hybrid; bytes
# inline for small outputs, digest for verification of full bytes).
_REQUIRED_FIELDS = {
    "type",
    "tool_call_id",
    "tool_name",
    "params",
    "success",
    "output",
    "output_digest",
    "output_bytes_len",
    "duration_ms",
    "started_at_ns",
    "completed_at_ns",
    "task_name",
    "provider_id",
    "scope_id",
}


def validate_effect_v1(obj: Object, r: Resolver) -> Failure | None:
    body = obj.body
    missing = _REQUIRED_FIELDS - set(body.keys())
    if missing:
        return Failure(
            "schema",
            f"shepherd/effect/v1 missing required fields: {sorted(missing)}",
        )

    if not isinstance(body["output_digest"], str) or not body["output_digest"].startswith("sha256:"):
        return Failure("schema", "output_digest must be a `sha256:<hex>` string")

    if not isinstance(body["output_bytes_len"], int) or body["output_bytes_len"] < 0:
        return Failure("schema", "output_bytes_len must be a non-negative integer")

    return None


profile = Profile(
    name="shepherd",
    validators={
        "shepherd/effect/v1": validate_effect_v1,
    },
)
