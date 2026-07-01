from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.kernel import elaborate_publication_experimental
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.experimental import Forward, TerminalDelay, TerminalFork
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import Delayed
from shepherd_kernel_v3_reference.source.syntax import Abort, Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.trace.machine import TraceSession, run_trace
from shepherd_kernel_v3_reference.trace.records import (
    ContinuationDelay,
    ContinuationPending,
    ContinuationResume,
    EffectCapture,
    EffectDeclaration,
    ForkBranch,
    HandlerForward,
    HandlerSelection,
    ResumeReturn,
    ResumptionHandle,
    SelectionClosed,
    TerminalResumeResult,
)
from shepherd_kernel_v3_reference.trace.serde import trace_from_json, trace_to_json
from shepherd_kernel_v3_reference.trace.validate import (
    TraceValidationError,
    validate_core_trace,
    validate_generated_trace_against_program,
    validate_publication_experimental_trace,
    validate_publication_experimental_trace_prefix,
)


def install(effect_kind: str, body, handler_id: str = "h.v1") -> StaticHandlerInstall:
    return StaticHandlerInstall(
        effect_kind=effect_kind,
        handler_id=handler_id,
        handled_result_schema=AnySchema(),
        body=body,
        payload_name="_payload",
    )


def test_publication_trace_validator_accepts_forward_completion() -> None:
    term = Handle(
        Handle(
            Perform("eff.a", Lit("payload")),
            HandlerEnv((install("eff.a", Forward(), "h.inner"),)),
        ),
        HandlerEnv((install("eff.a", Return(Lit("outer")), "h.outer"),)),
    )

    trace = run_trace(elaborate_publication_experimental(term)).trace

    validate_publication_experimental_trace(trace)
    with pytest.raises(TraceValidationError, match="unknown trace record"):
        validate_core_trace(trace)


def test_generated_trace_validator_dispatches_to_publication_profile() -> None:
    term = Handle(
        Handle(
            Perform("eff.a", Lit("payload")),
            HandlerEnv((install("eff.a", Forward(), "h.inner"),)),
        ),
        HandlerEnv((install("eff.a", Return(Lit("outer")), "h.outer"),)),
    )
    kernel = elaborate_publication_experimental(term)
    trace = run_trace(kernel).trace

    validate_generated_trace_against_program(kernel, trace)


def test_publication_trace_validator_accepts_pending_resume_prefix_and_completion() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()

    assert isinstance(result.outcome, Delayed)
    validate_publication_experimental_trace_prefix(session.trace)

    result.outcome.pending.apply("resumed")
    validate_publication_experimental_trace(session.trace)


def test_publication_completed_validator_rejects_pending_prefix() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()

    assert isinstance(result.outcome, Delayed)
    with pytest.raises(TraceValidationError, match="open selected paths"):
        validate_publication_experimental_trace(session.trace)

    validate_generated_trace_against_program(
        elaborate_publication_experimental(term),
        session.trace,
        completed=False,
    )


def test_publication_trace_validator_accepts_terminal_fork_completion() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork(
                        (
                            ("branch:A", Lit("value-A")),
                            ("branch:B", Lit("value-B")),
                        )
                    ),
                    "h.fork",
                ),
            )
        ),
    )

    trace = run_trace(elaborate_publication_experimental(term)).trace

    validate_publication_experimental_trace(trace)


def test_generated_trace_validator_accepts_completed_publication_fork() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("value-A")),)),
                    "h.fork",
                ),
            )
        ),
    )
    kernel = elaborate_publication_experimental(term)
    trace = run_trace(kernel).trace

    validate_generated_trace_against_program(kernel, trace)


def test_publication_trace_validator_rejects_terminal_resume_to_handler() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    trace = tuple(
        replace(record, returns_to_handler=True) if isinstance(record, ContinuationResume) else record
        for record in session.trace
    )

    with pytest.raises(TraceValidationError, match="pending resume is terminal"):
        validate_publication_experimental_trace(trace)


def test_publication_trace_validator_rejects_missing_resumption_handle() -> None:
    term = Handle(
        Perform("eff.a", Lit("payload")),
        HandlerEnv((install("eff.a", Return(Lit("handled")), "h.return"),)),
    )
    trace = tuple(
        record
        for record in run_trace(elaborate_publication_experimental(term)).trace
        if not isinstance(record, ResumptionHandle)
    )

    with pytest.raises(TraceValidationError, match="selected path mismatch|one handle"):
        validate_publication_experimental_trace(trace)


def test_publication_trace_validator_rejects_bogus_capture_path() -> None:
    term = Handle(
        Perform("eff.a", Lit("payload")),
        HandlerEnv((install("eff.a", Return(Lit("handled")), "h.return"),)),
    )
    trace = tuple(
        replace(record, selection_path_ref="path:bogus") if isinstance(record, EffectCapture) else record
        for record in run_trace(elaborate_publication_experimental(term)).trace
    )

    with pytest.raises(TraceValidationError, match="selected path mismatch"):
        validate_publication_experimental_trace(trace)


def test_publication_trace_validator_rejects_missing_terminal_result() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    trace = tuple(record for record in session.trace if not isinstance(record, TerminalResumeResult))

    with pytest.raises(TraceValidationError, match="TerminalResumeResult|open selected paths"):
        validate_publication_experimental_trace(trace)


def test_publication_trace_validator_rejects_duplicate_pending_resume() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    resume = next(
        record
        for record in session.trace
        if isinstance(record, ContinuationResume) and record.source_record_type == "ContinuationPending"
    )
    duplicate = replace(resume, ref="resume:duplicate")

    insert_at = tuple(session.trace).index(resume) + 1
    trace = tuple(session.trace[:insert_at]) + (duplicate,) + tuple(session.trace[insert_at:])

    with pytest.raises(TraceValidationError, match="source resumed twice"):
        validate_publication_experimental_trace(trace)


def test_publication_trace_accepts_resumed_selection_closed_without_resume_return() -> None:
    term = Handle(
        Handle(
            Let(
                "x",
                Perform("eff.inner", Lit(None)),
                Perform("eff.outer", Lit("after-resume")),
            ),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let("r", Resume(Lit("worker-value")), Return(Var("r"))),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
    )
    trace = run_trace(elaborate_publication_experimental(term)).trace

    assert any(isinstance(record, ContinuationResume) for record in trace)
    assert any(isinstance(record, SelectionClosed) for record in trace)
    assert not any(isinstance(record, ResumeReturn) for record in trace)
    validate_publication_experimental_trace(trace)


def test_publication_trace_accepts_core_lifecycle_inside_fork_branch() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Perform("eff.b", Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("fork-value")),)),
                    "h.fork",
                ),
                install(
                    "eff.b",
                    Let("r", Resume(Lit("resumed-b")), Return(Var("r"))),
                    "h.b",
                ),
            )
        ),
    )
    trace = run_trace(elaborate_publication_experimental(term)).trace

    assert any(
        isinstance(record, ContinuationResume)
        and record.source_record_type == "ResumptionHandle"
        and record.branch_ref == "branch:A"
        for record in trace
    )
    validate_publication_experimental_trace(trace)


def test_publication_trace_rejects_forward_declaration_or_binding_mismatch() -> None:
    term = Handle(
        Handle(
            Perform("eff.a", Lit("payload")),
            HandlerEnv((install("eff.a", Forward(), "h.inner"),)),
        ),
        HandlerEnv((install("eff.a", Return(Lit("outer")), "h.outer"),)),
    )
    trace = run_trace(elaborate_publication_experimental(term)).trace

    bad_declaration = tuple(
        replace(record, declaration_ref="declaration:bogus") if isinstance(record, HandlerForward) else record
        for record in trace
    )
    with pytest.raises(TraceValidationError, match="forward declaration"):
        validate_publication_experimental_trace(bad_declaration)

    bad_binding = tuple(
        replace(record, skipped_binding_ref="install:bogus") if isinstance(record, HandlerForward) else record
        for record in trace
    )
    with pytest.raises(TraceValidationError, match="forward binding"):
        validate_publication_experimental_trace(bad_binding)


def test_publication_trace_rejects_selection_closed_reason_disagreement() -> None:
    term = Handle(
        Handle(
            Perform("eff.inner", Lit(None)),
            HandlerEnv(
                (
                    install(
                        "eff.inner",
                        Let(
                            "x",
                            Perform("eff.outer", Lit("ask")),
                            Return(Var("x")),
                        ),
                        "h.inner",
                    ),
                )
            ),
        ),
        HandlerEnv((install("eff.outer", Abort(Lit("outer-abort")), "h.outer"),)),
    )
    trace = run_trace(elaborate_publication_experimental(term)).trace
    bad_trace = tuple(
        replace(record, reason="abandoned") if isinstance(record, SelectionClosed) else record for record in trace
    )

    with pytest.raises(TraceValidationError, match="reason disagrees"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_missing_fork_branch_materialization() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork(
                        (
                            ("branch:A", Lit("value-A")),
                            ("branch:B", Lit("value-B")),
                        )
                    ),
                    "h.fork",
                ),
            )
        ),
    )
    trace = run_trace(elaborate_publication_experimental(term)).trace
    branch_b_source = next(
        record.ref for record in trace if isinstance(record, ForkBranch) and record.branch_ref == "branch:B"
    )
    bad_trace = tuple(
        record
        for record in trace
        if getattr(record, "branch_ref", None) != "branch:B" and getattr(record, "source_ref", None) != branch_b_source
    )

    with pytest.raises(TraceValidationError, match="fork branch"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_duplicate_fork_branch_materialization() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("value-A")),)),
                    "h.fork",
                ),
            )
        ),
    )
    trace = run_trace(elaborate_publication_experimental(term)).trace
    branch = next(record for record in trace if isinstance(record, ForkBranch))
    duplicate = replace(branch, ref="fork-branch:duplicate")
    insert_at = tuple(trace).index(branch) + 1
    bad_trace = tuple(trace[:insert_at]) + (duplicate,) + tuple(trace[insert_at:])

    with pytest.raises(TraceValidationError, match="fork branch materialized more than once"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_duplicate_selection_for_declaration() -> None:
    term = Handle(
        Perform("eff.a", Lit("payload")),
        HandlerEnv((install("eff.a", Return(Lit("handled")), "h.return"),)),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    selection = next(record for record in trace if isinstance(record, HandlerSelection))
    handle = next(record for record in trace if isinstance(record, ResumptionHandle))
    capture = next(record for record in trace if isinstance(record, EffectCapture))
    duplicate_selection = replace(selection, ref="selection:duplicate")
    duplicate_handle = replace(
        handle,
        ref="resumption:duplicate",
        selection_ref=duplicate_selection.ref,
    )
    duplicate_path = f"path:{duplicate_selection.ref}/{duplicate_handle.ref}/{capture.branch_ref}"
    duplicate_capture = replace(
        capture,
        ref="capture:duplicate",
        selection_ref=duplicate_selection.ref,
        selection_path_ref=duplicate_path,
    )

    with pytest.raises(TraceValidationError, match="previous selection was not forwarded"):
        validate_publication_experimental_trace(trace + (duplicate_selection, duplicate_handle, duplicate_capture))


def test_publication_trace_rejects_unforwarded_second_selection_for_declaration() -> None:
    term = Handle(
        Perform("eff.a", Lit("payload")),
        HandlerEnv((install("eff.a", Return(Lit("handled")), "h.return"),)),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    selection = next(record for record in trace if isinstance(record, HandlerSelection))
    handle = next(record for record in trace if isinstance(record, ResumptionHandle))
    capture = next(record for record in trace if isinstance(record, EffectCapture))
    second_selection = replace(
        selection,
        ref="selection:other-binding",
        selected_binding_ref="install:other",
        handler_id="h.other",
    )
    second_handle = replace(
        handle,
        ref="resumption:other-binding",
        selection_ref=second_selection.ref,
    )
    second_path = f"path:{second_selection.ref}/{second_handle.ref}/{capture.branch_ref}"
    second_capture = replace(
        capture,
        ref="capture:other-binding",
        selection_ref=second_selection.ref,
        selection_path_ref=second_path,
    )

    with pytest.raises(TraceValidationError, match="previous selection was not forwarded"):
        validate_publication_experimental_trace(trace + (second_selection, second_handle, second_capture))


def test_publication_trace_rejects_selection_before_prior_forward_closure() -> None:
    term = Handle(
        Handle(
            Perform("eff.a", Lit("payload")),
            HandlerEnv((install("eff.a", Forward(), "h.inner"),)),
        ),
        HandlerEnv((install("eff.a", Return(Lit("outer")), "h.outer"),)),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    forward_idx = next(idx for idx, record in enumerate(trace) if isinstance(record, HandlerForward))
    outer_selection_idx = next(
        idx for idx, record in enumerate(trace) if isinstance(record, HandlerSelection) and idx > forward_idx
    )
    outer_capture_idx = next(
        idx for idx, record in enumerate(trace) if isinstance(record, EffectCapture) and idx > outer_selection_idx
    )
    bad_trace = (
        trace[:forward_idx]
        + trace[outer_selection_idx:outer_capture_idx]
        + trace[forward_idx:outer_selection_idx]
        + trace[outer_capture_idx:]
    )

    with pytest.raises(TraceValidationError, match="previous selection was not forwarded"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_duplicate_forward_for_selected_path() -> None:
    term = Handle(
        Handle(
            Perform("eff.a", Lit("payload")),
            HandlerEnv((install("eff.a", Forward(), "h.inner"),)),
        ),
        HandlerEnv((install("eff.a", Return(Lit("outer")), "h.outer"),)),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    forward = next(record for record in trace if isinstance(record, HandlerForward))
    duplicate = replace(forward, ref="forward:duplicate")
    insert_at = trace.index(forward) + 1
    bad_trace = trace[:insert_at] + (duplicate,) + trace[insert_at:]

    with pytest.raises(TraceValidationError, match="forwarded more than once"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_missing_continuation_delay() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    trace = tuple(record for record in session.trace if not isinstance(record, ContinuationDelay))

    with pytest.raises(TraceValidationError, match="ContinuationDelay"):
        validate_publication_experimental_trace(trace)


def test_publication_prefix_accepts_before_continuation_delay() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    pending_idx = next(idx for idx, record in enumerate(session.trace) if isinstance(record, ContinuationPending))

    validate_publication_experimental_trace_prefix(tuple(session.trace[: pending_idx + 1]))


def test_publication_prefix_accepts_after_continuation_delay() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    delay_idx = next(idx for idx, record in enumerate(session.trace) if isinstance(record, ContinuationDelay))

    validate_publication_experimental_trace_prefix(tuple(session.trace[: delay_idx + 1]))


def test_publication_prefix_rejects_pending_resume_without_continuation_delay() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    trace = tuple(record for record in session.trace if not isinstance(record, ContinuationDelay))
    pending_resume_idx = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, ContinuationResume) and record.source_record_type == "ContinuationPending"
    )

    with pytest.raises(TraceValidationError, match="requires ContinuationDelay"):
        validate_publication_experimental_trace_prefix(trace[: pending_resume_idx + 1])


def test_publication_prefix_rejects_terminal_result_without_continuation_delay() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    trace = tuple(record for record in session.trace if not isinstance(record, ContinuationDelay))
    terminal_result_idx = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, TerminalResumeResult) and record.source_record_type == "ContinuationPending"
    )

    with pytest.raises(TraceValidationError, match="requires ContinuationDelay"):
        validate_publication_experimental_trace_prefix(trace[: terminal_result_idx + 1])


def test_publication_trace_rejects_duplicate_continuation_delay() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    trace = tuple(session.trace)
    delay = next(record for record in trace if isinstance(record, ContinuationDelay))
    duplicate = replace(delay, ref="delay:duplicate")
    insert_at = trace.index(delay) + 1
    bad_trace = trace[:insert_at] + (duplicate,) + trace[insert_at:]

    with pytest.raises(TraceValidationError, match="duplicate delay"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_continuation_delay_after_pending_resume() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Return(Var("y"))),
        HandlerEnv((install("eff.a", TerminalDelay(Lit("waiting")), "h.delay"),)),
    )
    session = TraceSession(elaborate_publication_experimental(term))
    result = session.run()
    assert isinstance(result.outcome, Delayed)
    result.outcome.pending.apply("resumed")
    trace = tuple(session.trace)
    delay = next(record for record in trace if isinstance(record, ContinuationDelay))
    pending_resume_idx = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, ContinuationResume) and record.source_record_type == "ContinuationPending"
    )
    late_delay = replace(delay, ref="delay:late")
    bad_trace = trace[: pending_resume_idx + 1] + (late_delay,) + trace[pending_resume_idx + 1 :]

    with pytest.raises(TraceValidationError, match="after pending source resume"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_branch_records_after_terminal_result() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Perform("eff.b", Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("fork-value")),)),
                    "h.fork",
                ),
                install(
                    "eff.b",
                    Let("r", Resume(Lit("resumed-b")), Return(Var("r"))),
                    "h.b",
                ),
            )
        ),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    branch_record_start = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, EffectDeclaration) and record.branch_ref == "branch:A"
    )
    terminal_result_idx = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, TerminalResumeResult) and record.source_record_type == "ForkBranch"
    )
    bad_trace = (
        trace[:branch_record_start] + trace[terminal_result_idx:] + trace[branch_record_start:terminal_result_idx]
    )

    with pytest.raises(TraceValidationError, match="active branch scope"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_prefix_rejects_premature_fork_terminal_result() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Perform("eff.b", Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("fork-value")),)),
                    "h.fork",
                ),
                install(
                    "eff.b",
                    Let("r", Resume(Lit("resumed-b")), Return(Var("r"))),
                    "h.b",
                ),
            )
        ),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    callable_resume_idx = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, ContinuationResume) and record.source_record_type == "ResumptionHandle"
    )
    terminal_result = next(
        record
        for record in trace
        if isinstance(record, TerminalResumeResult) and record.source_record_type == "ForkBranch"
    )
    bad_prefix = trace[: callable_resume_idx + 1] + (terminal_result,)

    with pytest.raises(TraceValidationError, match="open selected paths|open callable"):
        validate_publication_experimental_trace_prefix(bad_prefix)


def test_publication_prefix_rejects_nested_same_label_records_after_inner_terminal_result() -> None:
    term = Handle(
        Let(
            "x",
            Perform("eff.a", Lit("payload-a")),
            Let("y", Perform("eff.b", Var("x")), Perform("eff.c", Var("y"))),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("value-a")),)),
                    "h.a",
                ),
                install(
                    "eff.b",
                    TerminalFork((("branch:A", Lit("value-b")),)),
                    "h.b",
                ),
                install(
                    "eff.c",
                    Let("r", Resume(Lit("resumed-c")), Return(Var("r"))),
                    "h.c",
                ),
            )
        ),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    inner_terminal_idx = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, TerminalResumeResult) and record.source_record_type == "ForkBranch"
    )
    inner_body_idx = next(
        idx
        for idx, record in enumerate(trace)
        if isinstance(record, EffectDeclaration) and record.effect_kind == "eff.c"
    )
    bad_prefix = (
        trace[:inner_body_idx]
        + trace[inner_terminal_idx : inner_terminal_idx + 1]
        + trace[inner_body_idx:inner_terminal_idx]
    )

    with pytest.raises(TraceValidationError, match="branch scope mismatch"):
        validate_publication_experimental_trace_prefix(bad_prefix)


def test_publication_trace_rejects_wrong_branch_scope_ref() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Perform("eff.b", Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("fork-value")),)),
                    "h.fork",
                ),
                install("eff.b", Return(Lit("handled-b")), "h.b"),
            )
        ),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    bad_trace = tuple(
        replace(record, branch_scope_ref="resume:stale")
        if isinstance(record, EffectDeclaration) and record.branch_ref == "branch:A"
        else record
        for record in trace
    )

    with pytest.raises(TraceValidationError, match="branch scope mismatch"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_trace_rejects_open_branch_scope() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Perform("eff.b", Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("fork-value")),)),
                    "h.fork",
                ),
                install("eff.b", Return(Lit("handled-b")), "h.b"),
            )
        ),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    bad_trace = tuple(
        record
        for record in trace
        if not (isinstance(record, TerminalResumeResult) and record.source_record_type == "ForkBranch")
    )

    with pytest.raises(TraceValidationError, match="TerminalResumeResult|open branch"):
        validate_publication_experimental_trace(bad_trace)


def test_publication_prefix_rejects_terminal_result_for_callable_resume() -> None:
    term = Handle(
        Perform("eff.a", Lit("payload")),
        HandlerEnv((install("eff.a", Resume(Lit("resumed")), "h.resume"),)),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    resume = next(record for record in trace if isinstance(record, ContinuationResume))
    prefix = trace[: trace.index(resume) + 1] + (
        TerminalResumeResult(
            ref="terminal-result:callable",
            resume_ref=resume.ref,
            source_ref=resume.source_ref,
            source_record_type="ResumptionHandle",
            selection_path_ref=resume.selection_path_ref,
            branch_ref=resume.branch_ref,
            value="resumed",
        ),
    )

    with pytest.raises(TraceValidationError, match="TerminalResumeResult"):
        validate_publication_experimental_trace_prefix(prefix)


def test_publication_nested_terminal_fork_is_prefix_not_completed() -> None:
    term = Handle(
        Let(
            "x",
            Perform("eff.a", Lit("payload-a")),
            Let("y", Perform("eff.b", Var("x")), Return(Var("y"))),
        ),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("value-a")),)),
                    "h.a",
                ),
                install(
                    "eff.b",
                    TerminalFork((("branch:A", Lit("value-b")),)),
                    "h.b",
                ),
            )
        ),
    )
    kernel = elaborate_publication_experimental(term)
    trace = run_trace(kernel).trace

    validate_generated_trace_against_program(kernel, trace, completed=False)
    with pytest.raises(TraceValidationError, match="did not complete"):
        validate_generated_trace_against_program(kernel, trace)
    with pytest.raises(TraceValidationError, match="TerminalResumeResult"):
        validate_publication_experimental_trace(trace)


def test_publication_branch_scope_ref_json_round_trip() -> None:
    term = Handle(
        Let("y", Perform("eff.a", Lit("payload")), Perform("eff.b", Var("y"))),
        HandlerEnv(
            (
                install(
                    "eff.a",
                    TerminalFork((("branch:A", Lit("fork-value")),)),
                    "h.fork",
                ),
                install("eff.b", Return(Lit("handled-b")), "h.b"),
            )
        ),
    )
    trace = tuple(run_trace(elaborate_publication_experimental(term)).trace)
    encoded = trace_to_json(trace)

    assert not any("branch_scope_ref" in item for item in encoded if item.get("branch_ref") == "branch:root")
    assert any(item.get("branch_scope_ref") for item in encoded)
    assert trace_from_json(encoded) == trace
