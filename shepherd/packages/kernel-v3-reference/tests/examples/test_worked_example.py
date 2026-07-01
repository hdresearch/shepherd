"""§13 worked-example end-to-end test.

The §29 type-discipline check is included: the worker's llm.generate
resumption must be typed by Draft, and the runtime schema check rejects
the Prompt-for-Draft swap.
"""

import pytest

from shepherd_kernel_v3_reference.examples.worked import build_program, build_registry
from shepherd_kernel_v3_reference.kernel import elaborate, run_kernel
from shepherd_kernel_v3_reference.schemas import ValidationError
from shepherd_kernel_v3_reference.source.eval_direct import run
from shepherd_kernel_v3_reference.source.outcomes import Completed
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.records import EffectDeclaration
from shepherd_kernel_v3_reference.trace.validate import validate_core_trace


def test_worked_example_completes_with_expected_draft() -> None:
    program = build_program()
    outcome = run(program, registry=build_registry())
    # Expected trace through the example:
    #   approval.request("draft section") -> "approved: draft section"
    #   model.call("approved: draft section") -> "draft-of: approved: draft section"
    #   resume(worker) with that Draft -> worker returns y = the Draft -> R = Draft.
    #   audit.log(section) -> Unit (ack)
    #   supervisor returns section -> Draft propagates to top.
    expected = {"kind": "Draft", "text": "draft-of: approved: draft section"}
    assert outcome == Completed(expected)


def test_worked_example_kernel_and_trace_match_direct() -> None:
    program = build_program()
    registry = build_registry()
    kernel = elaborate(program, registry=registry)
    expected = run(program, registry=registry)

    assert run_kernel(kernel) == expected
    trace_result = run_trace(kernel)
    assert trace_result.outcome == expected
    validate_core_trace(trace_result.trace)
    audit = next(
        record
        for record in trace_result.trace
        if isinstance(record, EffectDeclaration) and record.effect_kind == "audit.log"
    )
    assert isinstance(expected, Completed)
    assert audit.payload == {
        "kind": "AuditEntry",
        "section": expected.value,
    }


def test_swap_prompt_for_draft_at_resume_is_rejected_by_schema() -> None:
    # §29: llm.generate resumes with Draft. Resuming the worker with
    # the approved_prompt (a Prompt) violates the operation-result
    # schema for llm.generate.
    program = build_program(supervisor_resume_with="approved_prompt")
    with pytest.raises(ValidationError, match="resume.*llm.generate"):
        run(program, registry=build_registry())
