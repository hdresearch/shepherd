import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from shepherd_kernel_v3_reference.kernel import elaborate, run_kernel
from shepherd_kernel_v3_reference.kernel.continuation_objects import continuation_object_to_json
from shepherd_kernel_v3_reference.semantic import CanonicalRefMap, build_initial_transition_batch
from shepherd_kernel_v3_reference.source.eval_direct import run
from shepherd_kernel_v3_reference.source.outcomes import Completed, Suspended
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.serde import trace_from_json, trace_to_json
from shepherd_kernel_v3_reference.trace.validate import (
    validate_core0_trace,
    validate_core0_trace_prefix,
    validate_core_a_trace,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "golden_traces.json"
GOLDEN_CASES_PATH = Path(__file__).parents[1] / "golden_cases.py"
GOLDEN_CASES_SPEC = importlib.util.spec_from_file_location(
    "kernel_v3_reference_golden_cases",
    GOLDEN_CASES_PATH,
)
assert GOLDEN_CASES_SPEC is not None
assert GOLDEN_CASES_SPEC.loader is not None
GOLDEN_CASES_MODULE = importlib.util.module_from_spec(GOLDEN_CASES_SPEC)
sys.modules[GOLDEN_CASES_SPEC.name] = GOLDEN_CASES_MODULE
GOLDEN_CASES_SPEC.loader.exec_module(GOLDEN_CASES_MODULE)
assert isinstance(GOLDEN_CASES_MODULE, ModuleType)
golden_cases = GOLDEN_CASES_MODULE.golden_cases


def test_golden_trace_corpus_matches_generated_traces() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text())
    cases_by_name = {case.name: case for case in golden_cases()}

    assert fixture["schema_version"] == 3
    assert {case["case"] for case in fixture["cases"]} == set(cases_by_name)

    for entry in fixture["cases"]:
        case = cases_by_name[entry["case"]]
        kernel = elaborate(case.term)
        direct = run(case.term)
        machine = run_kernel(kernel)
        traced = run_trace(kernel, include_debug_evidence=True)
        fixture_trace = trace_from_json(entry["trace"])

        assert entry["boundary"] == case.boundary
        assert entry["completed"] is case.completed
        evidence = traced.require_debug_evidence()
        assert entry["program_ref"] == evidence.program_ref
        assert entry["outcome"] == _outcome_summary(traced.outcome)
        assert _outcomes_agree(direct, machine)
        assert _outcomes_agree(direct, traced.outcome)
        assert trace_to_json(traced.trace) == entry["trace"]
        assert entry["continuation_root_refs"] == list(evidence.continuation_root_refs)
        assert _continuation_objects_to_json(traced) == entry["continuation_objects"]
        assert fixture_trace == traced.trace
        build_initial_transition_batch(
            program_ref=entry["program_ref"],
            transition_id=f"transition:initial:{entry['case']}",
            records=tuple(entry["trace"]),
            ref_map=CanonicalRefMap(),
            continuation_objects=tuple(entry["continuation_objects"]),
        )
        _validate_boundary(entry["boundary"], fixture_trace)


def _validate_boundary(boundary: str, trace) -> None:
    if boundary == "core0":
        validate_core0_trace(trace)
        validate_core_a_trace(trace)
        return
    if boundary == "core0_prefix":
        validate_core0_trace_prefix(trace)
        return
    if boundary == "core_a":
        validate_core_a_trace(trace)
        return
    raise AssertionError(f"unknown golden boundary: {boundary!r}")


def _outcome_summary(outcome) -> dict[str, object]:
    if isinstance(outcome, Completed):
        return {"type": "Completed", "value": outcome.value}
    if isinstance(outcome, Suspended):
        return {
            "type": "Suspended",
            "effect_kind": outcome.effect_kind,
            "payload": outcome.payload,
        }
    raise AssertionError(f"unknown outcome: {outcome!r}")


def _outcomes_agree(left, right) -> bool:
    if isinstance(left, Completed):
        return right == left
    if isinstance(left, Suspended) and isinstance(right, Suspended):
        return right.effect_kind == left.effect_kind and right.payload == left.payload
    return False


def _continuation_objects_to_json(result) -> list[dict[str, object]]:
    return sorted(
        (continuation_object_to_json(obj) for obj in result.require_debug_evidence().list_continuation_objects()),
        key=lambda obj: (
            obj["object_type"],
            obj.get("stack_ref", ""),
            obj.get("head_frame_ref", ""),
            obj.get("frame_kind", ""),
        ),
    )
