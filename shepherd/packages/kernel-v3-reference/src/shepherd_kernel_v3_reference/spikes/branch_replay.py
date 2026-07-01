"""Proof-facing pressure check for one-child branch replay.

This module is intentionally a spike, not a production replay API. It names the
small certificate surface needed to replay one terminal fork branch from a
durable continuation image and compare the generated suffix exactly.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from shepherd_kernel_v3_reference.kernel.continuations import ContinuationImage
from shepherd_kernel_v3_reference.kernel.elaborate import KernelProgram
from shepherd_kernel_v3_reference.kernel.recursive_machine import RecursiveKernelEvaluator
from shepherd_kernel_v3_reference.paths import source_path_ref
from shepherd_kernel_v3_reference.profiles import PUBLICATION_EXPERIMENTAL, SemanticProfile
from shepherd_kernel_v3_reference.semantic import AdmissionBasis, ObservedFrontier, OneShotKey, SourceGeneration
from shepherd_kernel_v3_reference.source.effects import EffectRegistry
from shepherd_kernel_v3_reference.source.outcomes import Completed
from shepherd_kernel_v3_reference.trace.machine import TraceResult, record_from_event
from shepherd_kernel_v3_reference.trace.records import (
    ContinuationResume,
    EffectDeclaration,
    ForkBranch,
    ForkSummary,
    TerminalResumeResult,
    TraceRecord,
)
from shepherd_kernel_v3_reference.trace.validate import (
    TraceValidationError,
    validate_publication_experimental_trace,
    validate_publication_experimental_trace_prefix,
)


class BranchReplayValidationError(ValueError):
    """Raised when a one-child branch replay certificate is malformed."""


@dataclass(frozen=True)
class ReplayInputToken:
    """External input admitted for one terminal fork branch resume."""

    token_id: str
    source_ref: str
    source_kind: str
    source_path_ref: str
    branch_ref: str
    branch_scope_ref: str
    resume_ref: str
    observed_frontier: ObservedFrontier
    input_value: Any
    idempotency_key: str
    one_shot_key: OneShotKey
    program_ref: str
    operation_result_schema_ref: str | None
    restart_continuation_ref: str
    continuation_ref: str
    handler_continuation_ref: str
    handler_dynamic_tail_ref: str
    worker_context_ref: str | None
    handler_context_ref: str | None
    next_trace_id_after_resume: int
    source_generation: SourceGeneration = field(default_factory=SourceGeneration)
    profile: SemanticProfile = PUBLICATION_EXPERIMENTAL

    def admission_basis(self) -> AdmissionBasis:
        if self.source_kind != "ForkBranch":
            raise BranchReplayValidationError("only ForkBranch replay inputs are supported")
        return AdmissionBasis(
            source_ref=self.source_ref,
            source_kind="ForkBranch",
            source_generation=self.source_generation,
            observed_frontier=self.observed_frontier,
            source_path_ref=self.source_path_ref,
            input_value_or_digest=self.input_value,
            idempotency_key=self.idempotency_key,
            one_shot_key=self.one_shot_key,
            profile=self.profile,
            program_ref=self.program_ref,
        )


@dataclass(frozen=True)
class SingleChildBranchReplayCertificate:
    """Certificate for exact replay of one terminal fork branch suffix."""

    program_ref: str
    profile: SemanticProfile
    parent_branch_ref: str
    child_branch_ref: str
    fork_summary_ref: str
    fork_branch_ref: str
    fork_index: int
    parent_prefix_records: tuple[TraceRecord, ...]
    child_suffix_records: tuple[TraceRecord, ...]
    terminal_result_ref: str
    terminal_value: Any
    replay_input: ReplayInputToken
    continuation_images: tuple[ContinuationImage, ...]

    @property
    def parent_prefix_refs(self) -> tuple[str, ...]:
        return _record_refs(self.parent_prefix_records)

    @property
    def child_suffix_refs(self) -> tuple[str, ...]:
        return _record_refs(self.child_suffix_records)

    @property
    def continuation_image_refs(self) -> tuple[str, ...]:
        return tuple(image.ref for image in self.continuation_images if image.ref is not None)


def build_single_child_branch_replay_certificate(
    result: TraceResult,
    child_branch_ref: str,
    *,
    profile: SemanticProfile = PUBLICATION_EXPERIMENTAL,
) -> SingleChildBranchReplayCertificate:
    """Build a replay certificate from a generated publication trace."""

    _validate_trace(result.trace)
    fork_branch, fork_branch_idx = _single_record(
        result.trace,
        ForkBranch,
        lambda record: record.branch_ref == child_branch_ref,
        f"ForkBranch for {child_branch_ref!r}",
    )
    if fork_branch.terminal_continuation_ref is None:
        raise BranchReplayValidationError("fork branch is missing terminal_continuation_ref")
    fork_summary = _record_by_ref(result.trace, fork_branch.fork_ref, ForkSummary)
    resume, resume_idx = _single_record(
        result.trace,
        ContinuationResume,
        lambda record: record.source_record_type == "ForkBranch" and record.source_ref == fork_branch.ref,
        f"ContinuationResume for {fork_branch.ref!r}",
    )
    terminal, terminal_idx = _single_record(
        result.trace,
        TerminalResumeResult,
        lambda record: record.source_record_type == "ForkBranch" and record.resume_ref == resume.ref,
        f"TerminalResumeResult for {resume.ref!r}",
    )
    if not (fork_branch_idx < resume_idx < terminal_idx):
        raise BranchReplayValidationError("fork branch replay records are out of order")
    declaration = _record_by_ref(result.trace, fork_branch.declaration_ref, EffectDeclaration)
    source_path = source_path_ref(fork_branch.selection_ref, fork_branch.ref, fork_branch.branch_ref)
    if resume.selection_path_ref != source_path:
        raise BranchReplayValidationError("fork branch resume uses an unexpected source path")

    parent_prefix_records = result.trace[: fork_branch_idx + 1]
    child_suffix_records = result.trace[fork_branch_idx + 1 : terminal_idx + 1]
    next_trace_id = _next_trace_id_after(resume.ref)
    evidence = result.require_debug_evidence()
    token = ReplayInputToken(
        token_id=f"replay-input:{fork_branch.ref}:0",
        source_ref=fork_branch.ref,
        source_kind="ForkBranch",
        source_path_ref=source_path,
        branch_ref=fork_branch.branch_ref,
        branch_scope_ref=resume.branch_scope_ref or "",
        resume_ref=resume.ref,
        observed_frontier=ObservedFrontier(_record_refs(parent_prefix_records)),
        input_value=resume.value,
        idempotency_key=f"idempotency:{fork_branch.ref}:0:{resume.ref}",
        one_shot_key=OneShotKey(f"oneshot:{fork_branch.ref}:0"),
        program_ref=evidence.program_ref,
        operation_result_schema_ref=declaration.operation_result_schema_ref,
        restart_continuation_ref=fork_branch.terminal_continuation_ref,
        continuation_ref=resume.continuation_ref,
        handler_continuation_ref=resume.handler_continuation_ref,
        handler_dynamic_tail_ref=resume.handler_dynamic_tail_ref,
        worker_context_ref=resume.worker_context_ref,
        handler_context_ref=resume.handler_context_ref,
        next_trace_id_after_resume=next_trace_id,
        profile=profile,
    )
    cert = SingleChildBranchReplayCertificate(
        program_ref=evidence.program_ref,
        profile=profile,
        parent_branch_ref=fork_summary.branch_ref,
        child_branch_ref=fork_branch.branch_ref,
        fork_summary_ref=fork_summary.ref,
        fork_branch_ref=fork_branch.ref,
        fork_index=fork_branch_idx,
        parent_prefix_records=parent_prefix_records,
        child_suffix_records=child_suffix_records,
        terminal_result_ref=terminal.ref,
        terminal_value=terminal.value,
        replay_input=token,
        continuation_images=tuple(result.continuation_images.values()),
    )
    validate_single_child_branch_replay_certificate(cert)
    return cert


def validate_single_child_branch_replay_certificate(
    cert: SingleChildBranchReplayCertificate,
) -> None:
    """Validate certificate-internal branch/replay consistency."""

    if cert.profile != cert.replay_input.profile:
        raise BranchReplayValidationError("certificate profile disagrees with replay input")
    if cert.program_ref != cert.replay_input.program_ref:
        raise BranchReplayValidationError("certificate program_ref disagrees with replay input")
    if cert.replay_input.source_kind != "ForkBranch":
        raise BranchReplayValidationError("only ForkBranch replay inputs are supported")
    if cert.replay_input.branch_ref != cert.child_branch_ref:
        raise BranchReplayValidationError("replay input branch_ref disagrees with certificate")
    if cert.replay_input.observed_frontier.record_refs != cert.parent_prefix_refs:
        raise BranchReplayValidationError("replay input observed frontier is not the parent prefix")
    if not cert.parent_prefix_records:
        raise BranchReplayValidationError("parent prefix is empty")
    if not isinstance(cert.parent_prefix_records[-1], ForkBranch):
        raise BranchReplayValidationError("parent prefix must end at the fork branch source")
    if cert.fork_index != len(cert.parent_prefix_records) - 1:
        raise BranchReplayValidationError("fork index does not point at parent prefix boundary")
    _validate_trace_prefix(cert.parent_prefix_records)
    fork_branch = cert.parent_prefix_records[-1]
    fork_summary = _record_by_ref(cert.parent_prefix_records, cert.fork_summary_ref, ForkSummary)
    declaration = _record_by_ref(cert.parent_prefix_records, fork_branch.declaration_ref, EffectDeclaration)
    if fork_summary.branch_refs != (cert.child_branch_ref,):
        raise BranchReplayValidationError("single-child replay requires exactly one fork branch")
    full_trace = cert.parent_prefix_records + cert.child_suffix_records
    _validate_trace(full_trace)
    if fork_summary.branch_ref != cert.parent_branch_ref:
        raise BranchReplayValidationError("parent branch ref mismatch")
    if fork_summary.ref != cert.fork_summary_ref:
        raise BranchReplayValidationError("fork summary ref mismatch")
    if (
        fork_summary.declaration_ref != fork_branch.declaration_ref
        or fork_summary.selection_ref != fork_branch.selection_ref
    ):
        raise BranchReplayValidationError("fork branch selection/declaration mismatch")
    if fork_summary.selection_path_ref != fork_branch.selection_path_ref:
        raise BranchReplayValidationError("fork branch selected path mismatch")
    if fork_summary.branch_scope_ref != fork_branch.branch_scope_ref:
        raise BranchReplayValidationError("fork branch parent scope mismatch")
    if fork_branch.ref != cert.fork_branch_ref:
        raise BranchReplayValidationError("fork branch ref mismatch")
    if fork_branch.branch_ref != cert.child_branch_ref:
        raise BranchReplayValidationError("fork branch child branch mismatch")
    if fork_branch.fork_ref != cert.fork_summary_ref:
        raise BranchReplayValidationError("fork summary ref mismatch")
    if cert.replay_input.source_ref != fork_branch.ref:
        raise BranchReplayValidationError("replay input source ref mismatch")
    if cert.replay_input.input_value != fork_branch.value:
        raise BranchReplayValidationError("replay input value disagrees with fork branch")
    if cert.replay_input.operation_result_schema_ref != declaration.operation_result_schema_ref:
        raise BranchReplayValidationError("replay input operation-result schema mismatch")
    if fork_branch.terminal_continuation_ref != cert.replay_input.restart_continuation_ref:
        raise BranchReplayValidationError("restart continuation ref mismatch")
    expected_path = source_path_ref(fork_branch.selection_ref, fork_branch.ref, fork_branch.branch_ref)
    if cert.replay_input.source_path_ref != expected_path:
        raise BranchReplayValidationError("replay input source path mismatch")

    if not cert.child_suffix_records or not isinstance(cert.child_suffix_records[0], ContinuationResume):
        raise BranchReplayValidationError("child suffix must start with a ContinuationResume")
    resume = cert.child_suffix_records[0]
    if resume.ref != cert.replay_input.resume_ref:
        raise BranchReplayValidationError("resume ref mismatch")
    if resume.branch_scope_ref is None:
        raise BranchReplayValidationError("fork branch resume is missing branch scope")
    if resume.branch_scope_ref != resume.ref:
        raise BranchReplayValidationError("fork branch resume scope must be its resume ref")
    if resume.branch_scope_ref != cert.replay_input.branch_scope_ref:
        raise BranchReplayValidationError("resume branch scope mismatch")
    if resume.source_ref != fork_branch.ref or resume.source_record_type != "ForkBranch":
        raise BranchReplayValidationError("resume does not consume the fork branch source")
    if resume.declaration_ref != fork_branch.declaration_ref or resume.selection_ref != fork_branch.selection_ref:
        raise BranchReplayValidationError("resume selection/declaration mismatch")
    if resume.selection_path_ref != cert.replay_input.source_path_ref:
        raise BranchReplayValidationError("resume selected path mismatch")
    if resume.branch_ref != cert.child_branch_ref:
        raise BranchReplayValidationError("resume branch mismatch")
    if resume.continuation_ref != fork_branch.continuation_ref:
        raise BranchReplayValidationError("resume continuation mismatch")
    if resume.continuation_ref != cert.replay_input.continuation_ref:
        raise BranchReplayValidationError("replay input continuation mismatch")
    if resume.handler_continuation_ref != cert.replay_input.handler_continuation_ref:
        raise BranchReplayValidationError("replay input handler continuation mismatch")
    if resume.handler_dynamic_tail_ref != cert.replay_input.handler_dynamic_tail_ref:
        raise BranchReplayValidationError("replay input handler dynamic tail mismatch")
    if resume.worker_context_ref != cert.replay_input.worker_context_ref:
        raise BranchReplayValidationError("replay input worker context mismatch")
    if resume.handler_context_ref != cert.replay_input.handler_context_ref:
        raise BranchReplayValidationError("replay input handler context mismatch")
    if resume.returns_to_handler:
        raise BranchReplayValidationError("fork branch resume must be terminal")
    if resume.value != cert.replay_input.input_value:
        raise BranchReplayValidationError("resume value disagrees with replay input")
    if cert.replay_input.next_trace_id_after_resume != _next_trace_id_after(resume.ref):
        raise BranchReplayValidationError("next trace id after resume is stale")

    terminal = cert.child_suffix_records[-1]
    if not isinstance(terminal, TerminalResumeResult):
        raise BranchReplayValidationError("child suffix must end with a TerminalResumeResult")
    if terminal.ref != cert.terminal_result_ref or terminal.value != cert.terminal_value:
        raise BranchReplayValidationError("terminal result mismatch")
    if terminal.resume_ref != resume.ref or terminal.source_ref != fork_branch.ref:
        raise BranchReplayValidationError("terminal result does not close the replayed source")
    if terminal.source_record_type != "ForkBranch":
        raise BranchReplayValidationError("terminal result source kind mismatch")
    if terminal.selection_path_ref != resume.selection_path_ref:
        raise BranchReplayValidationError("terminal result selected path mismatch")
    if terminal.branch_ref != cert.child_branch_ref:
        raise BranchReplayValidationError("terminal result branch mismatch")
    if terminal.branch_scope_ref != resume.branch_scope_ref:
        raise BranchReplayValidationError("terminal result branch scope mismatch")

    images = _image_catalog(cert.continuation_images)
    image = images.get(cert.replay_input.restart_continuation_ref)
    if image is None:
        raise BranchReplayValidationError("restart continuation image is missing")
    if image.program_ref != cert.program_ref:
        raise BranchReplayValidationError("restart image program_ref mismatch")
    if image.branch_ref != cert.child_branch_ref:
        raise BranchReplayValidationError("restart image branch_ref mismatch")
    if image.branch_scope_ref != cert.replay_input.branch_scope_ref:
        raise BranchReplayValidationError("restart image branch scope mismatch")
    if image.position != "value":
        raise BranchReplayValidationError("restart image position mismatch")
    if image.continuation_kind != "full":
        raise BranchReplayValidationError("restart image continuation kind mismatch")


def replay_single_child_branch_from_image(
    program: KernelProgram,
    cert: SingleChildBranchReplayCertificate,
    *,
    registry: EffectRegistry | None = None,
) -> tuple[TraceRecord, ...]:
    """Replay the certified child suffix from its durable terminal image."""

    validate_single_child_branch_replay_certificate(cert)
    images = _image_catalog(cert.continuation_images)
    token = cert.replay_input
    restart_image = images[token.restart_continuation_ref]
    records: list[TraceRecord] = []
    evaluator = RecursiveKernelEvaluator(
        program,
        registry=registry,
        event_sink=lambda event: records.append(record_from_event(event)),
    )
    for image in images.values():
        evaluator._register_continuation_image(image)
    evaluator._state.next_trace_id = token.next_trace_id_after_resume

    resume = ContinuationResume(
        ref=token.resume_ref,
        source_ref=token.source_ref,
        source_record_type="ForkBranch",
        declaration_ref=_fork_branch(cert).declaration_ref,
        selection_ref=_fork_branch(cert).selection_ref,
        selection_path_ref=token.source_path_ref,
        continuation_ref=token.continuation_ref,
        handler_continuation_ref=token.handler_continuation_ref,
        handler_dynamic_tail_ref=token.handler_dynamic_tail_ref,
        branch_ref=token.branch_ref,
        value=token.input_value,
        returns_to_handler=False,
        worker_context_ref=token.worker_context_ref,
        handler_context_ref=token.handler_context_ref,
        branch_scope_ref=token.branch_scope_ref,
    )
    outcome = evaluator._resume_value_from_image(
        restart_image,
        token.input_value,
        operation_result_schema_ref=token.operation_result_schema_ref,
        source_label=f"replay({token.source_ref!r})",
    )
    replayed: list[TraceRecord] = [resume, *records]
    if isinstance(outcome, Completed):
        replayed.append(
            TerminalResumeResult(
                ref=evaluator._fresh_ref("terminal-result"),
                resume_ref=token.resume_ref,
                source_ref=token.source_ref,
                source_record_type="ForkBranch",
                selection_path_ref=token.source_path_ref,
                branch_ref=token.branch_ref,
                value=outcome.value,
                branch_scope_ref=token.branch_scope_ref,
            )
        )
    replayed_trace = tuple(replayed)
    if replayed_trace != cert.child_suffix_records:
        raise BranchReplayValidationError("replayed child suffix does not match certificate")
    return replayed_trace


def _validate_trace(trace: tuple[TraceRecord, ...]) -> None:
    try:
        validate_publication_experimental_trace(trace)
    except TraceValidationError as exc:
        raise BranchReplayValidationError(str(exc)) from exc


def _validate_trace_prefix(trace: tuple[TraceRecord, ...]) -> None:
    try:
        validate_publication_experimental_trace_prefix(trace)
    except TraceValidationError as exc:
        raise BranchReplayValidationError(str(exc)) from exc


def _record_refs(records: tuple[TraceRecord, ...]) -> tuple[str, ...]:
    return tuple(record.ref for record in records)


def _record_by_ref(
    trace: tuple[TraceRecord, ...],
    ref: str,
    record_type: type[TraceRecord],
) -> TraceRecord:
    matches = [record for record in trace if isinstance(record, record_type) and record.ref == ref]
    if len(matches) != 1:
        raise BranchReplayValidationError(f"expected one {record_type.__name__} with ref {ref!r}")
    return matches[0]


def _single_record(
    trace: tuple[TraceRecord, ...],
    record_type: type[TraceRecord],
    predicate: Any,
    label: str,
) -> tuple[TraceRecord, int]:
    matches = [
        (record, idx) for idx, record in enumerate(trace) if isinstance(record, record_type) and predicate(record)
    ]
    if len(matches) != 1:
        raise BranchReplayValidationError(f"expected one {label}, found {len(matches)}")
    return matches[0]


def _fork_branch(cert: SingleChildBranchReplayCertificate) -> ForkBranch:
    record = cert.parent_prefix_records[-1]
    if not isinstance(record, ForkBranch):
        raise BranchReplayValidationError("parent prefix does not end in a fork branch")
    return record


def _image_catalog(images: tuple[ContinuationImage, ...]) -> Mapping[str, ContinuationImage]:
    catalog: dict[str, ContinuationImage] = {}
    for image in images:
        if image.ref is None:
            raise BranchReplayValidationError("continuation image is missing ref")
        if image.ref in catalog:
            raise BranchReplayValidationError(f"duplicate continuation image ref: {image.ref!r}")
        catalog[image.ref] = image
    return catalog


def _next_trace_id_after(ref: str) -> int:
    try:
        return int(ref.rsplit(":", 1)[1]) + 1
    except (IndexError, ValueError) as exc:
        raise BranchReplayValidationError(f"cannot derive trace cursor from ref {ref!r}") from exc
