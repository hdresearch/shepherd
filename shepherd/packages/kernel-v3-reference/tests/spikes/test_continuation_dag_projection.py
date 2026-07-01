from dataclasses import fields

from shepherd_kernel_v3_reference.source.outcomes import Completed
from shepherd_kernel_v3_reference.spikes.continuation_dag_projection import (
    CONTINUATION_DAG_OBJECT_SCHEMA_VERSION,
    profile_sequential_effects,
    run_trace_with_dag_projection,
    run_trace_with_shadow_dag_projection,
    sequential_handled_effect_program,
)
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.validate import validate_core_trace, validate_runtime_trace


def test_shadow_projection_preserves_existing_trace_records() -> None:
    program = sequential_handled_effect_program(2)

    baseline = run_trace(program, include_debug_evidence=True)
    shadow = run_trace_with_shadow_dag_projection(program)

    assert shadow.outcome == baseline.outcome == Completed("value")
    assert shadow.trace == baseline.trace
    assert _trace_continuation_refs(shadow.trace) <= set(shadow.continuation_ref_aliases)
    assert set(shadow.continuation_ref_aliases.values()) <= set(shadow.continuation_objects)


def test_dag_projection_refs_validate_and_resolve_to_root_objects() -> None:
    result = run_trace_with_dag_projection(sequential_handled_effect_program(3))

    assert result.outcome == Completed("value")
    validate_core_trace(result.trace)
    validate_runtime_trace(result.trace)
    refs = _trace_continuation_refs(result.trace)
    assert refs
    assert refs <= set(result.continuation_objects)
    for ref in refs:
        obj = result.get_continuation_object(ref)
        assert obj["object_schema_version"] == CONTINUATION_DAG_OBJECT_SCHEMA_VERSION
        assert obj["object_type"] == "root"
        assert obj["stack_ref"] in result.continuation_objects


def test_dag_projection_profiles_sequential_effects_without_recursive_blowup() -> None:
    rows = profile_sequential_effects((1, 5, 10, 25, 50))

    by_count = {row.effect_count: row for row in rows}
    assert by_count[50].trace_record_count == 300
    assert by_count[50].continuation_root_count == 250
    assert by_count[50].continuation_object_count < 2500
    assert by_count[50].max_object_json_bytes < 16_000
    assert by_count[50].total_object_json_bytes < 2_000_000
    assert by_count[50].continuation_object_count < by_count[25].continuation_object_count * 3
    assert by_count[50].total_object_json_bytes < by_count[25].total_object_json_bytes * 5


def _trace_continuation_refs(trace) -> set[str]:
    refs: set[str] = set()
    for record in trace:
        for field in fields(record):
            if field.name.endswith("continuation_ref"):
                value = getattr(record, field.name)
                if isinstance(value, str):
                    refs.add(value)
    return refs
