import json

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import Abort, Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace.machine import run_trace
from shepherd_kernel_v3_reference.trace.records import (
    ContinuationDelay,
    ContinuationPending,
    ForkBranch,
    ForkSummary,
    HandlerForward,
    SelectionClosed,
    TerminalResumeResult,
)
from shepherd_kernel_v3_reference.trace.serde import (
    TraceSerializationError,
    dumps_trace,
    loads_trace,
    trace_from_json,
    trace_record_from_json,
    trace_to_json,
)
from shepherd_kernel_v3_reference.trace.validate import validate_core_trace


def install(effect_kind: str, body, handler_id: str = "h.v1") -> StaticHandlerInstall:
    return StaticHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        payload_name="_payload",
        body=body,
    )


def test_trace_json_round_trips_core0_trace() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit(None)), Return(Var("y"))),
        HandlerEnv((install("eff.a", Let("r", Resume(Lit("value")), Return(Var("r")))),)),
    )
    result = run_trace(elaborate(term))

    encoded = trace_to_json(result.trace)
    decoded = trace_from_json(encoded)

    assert decoded == result.trace
    validate_core_trace(decoded)


def test_trace_json_round_trips_core_a_selection_closure() -> None:
    term = Handle(
        Handle(
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let("x", Perform("eff.outer", Lit("ask")), Return(Var("x"))),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("blocked")), "h.outer"),)),
    )
    result = run_trace(elaborate(term))

    assert any(isinstance(record, SelectionClosed) for record in result.trace)
    decoded = loads_trace(dumps_trace(result.trace))

    assert decoded == result.trace
    validate_core_trace(decoded)


def test_trace_json_text_is_stable_and_parseable() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv((install("eff.a", Return(Lit("handled"))),)),
    )
    result = run_trace(elaborate(term))

    encoded = dumps_trace(result.trace)

    assert encoded == dumps_trace(loads_trace(encoded))
    assert isinstance(json.loads(encoded), list)


def test_trace_json_round_trips_publication_control_records() -> None:
    records = (
        HandlerForward(
            ref="forward:0",
            declaration_ref="declaration:0",
            skipped_selection_ref="selection:1",
            skipped_binding_ref="install:2",
            skipped_selection_path_ref="path:selection:1/resumption:2/branch:root",
            branch_ref="branch:root",
        ),
        ContinuationPending(
            ref="pending:3",
            declaration_ref="declaration:0",
            selection_ref="selection:1",
            selection_path_ref="path:selection:1/resumption:2/branch:root",
            continuation_ref="kont:abc",
            operation_result_schema_ref=None,
            branch_ref="branch:root",
            reason="waiting",
            worker_context_ref="ctx:worker",
        ),
        ContinuationDelay(
            ref="delay:4",
            pending_ref="pending:3",
            reason="waiting",
        ),
        ForkSummary(
            ref="fork:5",
            declaration_ref="declaration:0",
            selection_ref="selection:1",
            selection_path_ref="path:selection:1/resumption:2/branch:root",
            branch_ref="branch:root",
            branch_refs=("branch:A", "branch:B"),
        ),
        ForkBranch(
            ref="fork-branch:6",
            fork_ref="fork:5",
            declaration_ref="declaration:0",
            selection_ref="selection:1",
            selection_path_ref="path:selection:1/resumption:2/branch:A",
            branch_ref="branch:A",
            continuation_ref="kont:abc",
            value="A",
        ),
        TerminalResumeResult(
            ref="terminal-result:7",
            resume_ref="resume:8",
            source_ref="fork-branch:6",
            source_record_type="ForkBranch",
            selection_path_ref="path:selection:1/fork-branch:6/branch:A",
            branch_ref="branch:A",
            value="done",
        ),
    )

    assert trace_from_json(trace_to_json(records)) == records
    assert loads_trace(dumps_trace(records)) == records


def test_trace_json_rejects_unknown_record_type() -> None:
    with pytest.raises(TraceSerializationError, match="unknown trace record_type"):
        trace_record_from_json({"record_type": "NoSuchRecord", "ref": "x"})


def test_trace_json_rejects_missing_required_field() -> None:
    with pytest.raises(TraceSerializationError, match="missing required fields"):
        trace_record_from_json({"record_type": "EffectDeclaration", "ref": "declaration:0"})
