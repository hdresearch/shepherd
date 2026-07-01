import functools
import json
from pathlib import Path

from shepherd_kernel_v3_reference.conformance import (
    artifact_from_trace_result,
    conformance_artifact_from_json,
    conformance_artifact_to_json,
    validate_conformance_artifact,
)
from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace.machine import run_trace

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "golden_run_trace_identity_artifacts.json"
# This is a semantic-ref lock, not a generated expectation. Do not update it for
# cache-only changes unless the changeset explicitly accepts a ref change.


def test_run_trace_identity_artifact_matches_pre_cache_fixture() -> None:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    entries = data["artifacts"]
    assert [entry["name"] for entry in entries] == ["sequential-handled-effect-identity-5"]

    artifact_json = entries[0]["artifact"]
    validate_conformance_artifact(conformance_artifact_from_json(artifact_json))

    assert conformance_artifact_to_json(_identity_artifact()) == artifact_json


def _identity_artifact():
    return artifact_from_trace_result(run_trace(_sequential_handled_effect_program(5), include_debug_evidence=True))


def _sequential_handled_effect_program(effect_count: int):
    term = functools.reduce(
        lambda body, idx: Let(f"y{idx}", Perform("eff.a", Lit({"i": idx})), body),
        reversed(range(effect_count)),
        Return(Var(f"y{effect_count - 1}")) if effect_count else Return(Lit(None)),
    )
    return elaborate(
        Handle(
            term,
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="run-trace-identity-golden.handler.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )
