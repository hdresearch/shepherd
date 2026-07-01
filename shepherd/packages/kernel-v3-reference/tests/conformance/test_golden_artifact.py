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

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "golden_conformance_artifacts.json"


def test_golden_conformance_artifact_validates_and_matches_builder() -> None:
    data = json.loads(_FIXTURE.read_text())
    entries = data["artifacts"]
    assert [entry["name"] for entry in entries] == [
        "sequential-handled-effect-runtime-with-continuations",
    ]

    artifact = conformance_artifact_from_json(entries[0]["artifact"])
    validate_conformance_artifact(artifact)

    assert conformance_artifact_to_json(_golden_artifact()) == entries[0]["artifact"]


def _golden_artifact():
    result = run_trace(
        elaborate(
            Handle(
                Let("x", Perform("eff.a", Lit({"i": 0})), Return(Var("x"))),
                HandlerEnv(
                    (
                        StaticHandlerInstall(
                            effect_kind="eff.a",
                            handler_id="conformance-artifact-test.handler.v1",
                            handled_result_schema=AnySchema(),
                            payload_name="_payload",
                            body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                        ),
                    )
                ),
            )
        ),
        include_debug_evidence=True,
    )
    return artifact_from_trace_result(result)
