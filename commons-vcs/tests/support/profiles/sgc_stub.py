"""sgc/receipt/v1 stub profile (Phase -1 spike).

NOT a real sgc receipt. The real receipt requires kernel0's
pin → admit → seal → verify → certify lineage. This validator accepts
a structural placeholder for cross-profile composition validation only;
the real sgc/receipt/v1 schema is sgc-team-owned and lands as part of
broader sgc adoption on commons-vcs (post-v2).

Per refactor.md §11 Phase -1 stub spec:
    body:  { decision: "approve" | "reject" | "defer", summary: <text> }
    edges: evidence → <shepherd-effect-id>  (at least one)
"""

from __future__ import annotations

from commons_vcs import Failure, Object, Profile, Resolver

_DECISIONS = {"approve", "reject", "defer"}


def validate_receipt_v1(obj: Object, r: Resolver) -> Failure | None:
    body = obj.body
    if "decision" not in body:
        return Failure("schema", "sgc/receipt/v1.body must contain `decision`")
    if body["decision"] not in _DECISIONS:
        return Failure(
            "schema",
            f"sgc/receipt/v1.body.decision must be one of {sorted(_DECISIONS)}",
        )
    if "summary" not in body or not isinstance(body["summary"], str):
        return Failure("schema", "sgc/receipt/v1.body must contain `summary` as a string")

    evidence_edges = [e for e in obj.edges if e.role == "evidence"]
    if not evidence_edges:
        return Failure(
            "schema",
            "sgc/receipt/v1 (stub) requires at least one `evidence` edge",
        )

    return None


profile = Profile(
    name="sgc_stub",
    validators={
        "sgc/receipt/v1": validate_receipt_v1,
    },
)
