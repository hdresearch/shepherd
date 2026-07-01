"""Spike: project live continuations into ref-addressed DAG objects.

This module is intentionally not a production continuation API. It pressure
checks whether the current evaluator's internal frame tuples can be projected
into small, content-addressed objects without recursively inlining child
continuations.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from shepherd_kernel_v3_reference.kernel.context import ExecutionContext
from shepherd_kernel_v3_reference.kernel.continuations import ContinuationImageKind
from shepherd_kernel_v3_reference.kernel.elaborate import KernelProgram, elaborate
from shepherd_kernel_v3_reference.kernel.frame_state import (
    BindFrame,
    Frame,
    HandlerFrame,
    HandlerReturnFrame,
    ResumeReturnFrame,
)
from shepherd_kernel_v3_reference.kernel.ir import Ref
from shepherd_kernel_v3_reference.kernel.recursive_machine import RecursiveKernelEvaluator
from shepherd_kernel_v3_reference.kernel.refs import content_ref
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.effects import EffectRegistry
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.outcomes import SourceOutcome
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Resume, Return, Var
from shepherd_kernel_v3_reference.source.values import Env
from shepherd_kernel_v3_reference.trace.machine import record_from_event
from shepherd_kernel_v3_reference.trace.records import TraceRecord

CONTINUATION_DAG_OBJECT_SCHEMA_VERSION = "shepherd_kernel_v3_reference.continuation-dag-object.spike.v0"
CONTINUATION_DAG_CONTROL_SCHEMA_VERSION = "shepherd_kernel_v3_reference.continuation-dag-control.spike.v0"

ContinuationObjectType = Literal["empty-stack", "stack-node", "frame", "root"]


@dataclass(frozen=True)
class ContinuationDagTraceResult:
    """Trace result emitted by a DAG-projection spike evaluator."""

    outcome: SourceOutcome
    trace: tuple[TraceRecord, ...]
    continuation_objects: Mapping[Ref, Mapping[str, Any]]
    continuation_root_refs: tuple[Ref, ...]
    program_ref: Ref
    continuation_ref_aliases: Mapping[Ref, Ref]

    def get_continuation_object(self, ref: Ref) -> Mapping[str, Any]:
        """Return one projected continuation object by semantic ref."""

        return self.continuation_objects[ref]


@dataclass(frozen=True)
class DagProjectionProfileRow:
    """One deterministic-ish sizing row for sequential effect projection."""

    effect_count: int
    trace_record_count: int
    continuation_root_count: int
    continuation_object_count: int
    total_object_json_bytes: int
    max_object_json_bytes: int
    elapsed_ms: float


class InMemoryContinuationObjectStore:
    """Minimal content-addressed object store for projected continuation DAGs."""

    def __init__(self) -> None:
        self._objects: dict[Ref, dict[str, Any]] = {}
        self.empty_stack_ref = self.put("empty-stack", {})

    @property
    def objects(self) -> Mapping[Ref, Mapping[str, Any]]:
        return dict(self._objects)

    def contains(self, ref: Ref) -> bool:
        return ref in self._objects

    def get(self, ref: Ref) -> Mapping[str, Any]:
        return self._objects[ref]

    def put(self, object_type: ContinuationObjectType, payload: Mapping[str, Any]) -> Ref:
        full_payload = {
            "object_schema_version": CONTINUATION_DAG_OBJECT_SCHEMA_VERSION,
            "object_type": object_type,
            **dict(payload),
        }
        ref = content_ref("continuation-object", full_payload)
        existing = self._objects.get(ref)
        if existing is not None and existing != full_payload:
            raise RuntimeError(f"continuation object ref collision: {ref!r}")
        self._objects[ref] = full_payload
        return ref


class ContinuationDagProjector:
    """Project evaluator frames into shared continuation DAG objects."""

    def __init__(
        self,
        evaluator: RecursiveKernelEvaluator,
        store: InMemoryContinuationObjectStore | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.store = store or InMemoryContinuationObjectStore()
        self.root_refs: list[Ref] = []
        self._frame_cache: dict[int, Ref] = {}
        self._stack_cache: dict[tuple[Ref, ...], Ref] = {(): self.store.empty_stack_ref}

    def project_root(
        self,
        kont,
        *,
        continuation_kind: ContinuationImageKind,
        context: ExecutionContext,
        result_schema_ref: Ref | None = None,
    ) -> Ref:
        stack_ref = self.project_stack(kont)
        root_ref = self.store.put(
            "root",
            {
                "program_ref": self.evaluator.program_ref,
                "branch_ref": self.evaluator._state.branch_ref,
                "branch_scope_ref": self.evaluator._state.branch_scope_ref,
                "position": "value",
                "continuation_kind": continuation_kind,
                "execution_context_ref": self.evaluator._context_ref(context),
                "execution_context": self.evaluator._context_payload(context),
                "result_schema_ref": result_schema_ref,
                "stack_ref": stack_ref,
                "required_schema_refs": tuple(sorted(self._refs_by_suffix(stack_ref, "_schema_ref"))),
                "code_identity_refs": tuple(sorted(self._code_identity_refs(stack_ref) | {self.evaluator.program_ref})),
            },
        )
        self.root_refs.append(root_ref)
        return root_ref

    def project_control_ref(self, kont: tuple[Frame, ...]) -> Ref:
        payload = {
            "control_schema_version": CONTINUATION_DAG_CONTROL_SCHEMA_VERSION,
            "program_ref": self.evaluator.program_ref,
            "branch_ref": self.evaluator._state.branch_ref,
            "branch_scope_ref": self.evaluator._state.branch_scope_ref,
            "position": "value",
            "stack_ref": self.project_stack(kont),
        }
        return content_ref("continuation-control", payload)

    def project_stack(self, kont: tuple[Frame, ...]) -> Ref:
        frame_refs = tuple(self.project_frame(frame) for frame in kont)
        cached = self._stack_cache.get(frame_refs)
        if cached is not None:
            return cached

        tail_ref = self.store.empty_stack_ref
        for head_ref in reversed(frame_refs):
            tail_ref = self.store.put(
                "stack-node",
                {
                    "head_frame_ref": head_ref,
                    "tail_stack_ref": tail_ref,
                },
            )
        self._stack_cache[frame_refs] = tail_ref
        return tail_ref

    def project_frame(self, frame: Frame) -> Ref:
        cache_key = id(frame)
        cached = self._frame_cache.get(cache_key)
        if cached is not None:
            return cached

        payload = self._frame_payload(frame)
        ref = self.store.put("frame", payload)
        self._frame_cache[cache_key] = ref
        return ref

    def _frame_payload(self, frame: Frame) -> Mapping[str, Any]:
        if isinstance(frame, BindFrame):
            binder_ref = self.evaluator._binder_ref(frame.binder_id)
            return {
                "frame": "bind",
                "binder_id": frame.binder_id,
                "binder_ref": binder_ref,
                "env_ref": self.evaluator._env_ref(frame.env),
                "env": frame.env.bindings,
                "context_ref": self.evaluator._context_ref(frame.context),
                "context": self.evaluator._context_payload(frame.context),
            }

        if isinstance(frame, HandlerFrame):
            return {
                "frame": "handler",
                "handler_env_ref": frame.handler_env_ref,
                "handler_env_def_ref": self.evaluator._handler_env_def_ref(frame.handler_env_ref),
                "region_ref": frame.region_ref,
                "env_ref": self.evaluator._env_ref(frame.env),
                "env": frame.env.bindings,
                "entry_context_ref": self.evaluator._context_ref(frame.entry_context),
                "entry_context": self.evaluator._context_payload(frame.entry_context),
                "outer_context_ref": self.evaluator._context_ref(frame.outer_context),
                "outer_context": self.evaluator._context_payload(frame.outer_context),
            }

        if isinstance(frame, HandlerReturnFrame):
            captured_control_ref = self.project_control_ref(frame.captured_kont)
            outer_control_ref = self.project_control_ref(frame.outer_kont)
            return {
                "frame": "handler-return",
                "install_ref": frame.install.install_ref,
                "install_def_ref": self.evaluator._install_ref(frame.install),
                "captured_stack_ref": self.project_stack(frame.captured_kont),
                "selected_handler_frame_ref": self.project_frame(frame.selected_handler_frame),
                "outer_stack_ref": self.project_stack(frame.outer_kont),
                "handler_env_ref": self.evaluator._env_ref(frame.handler_env),
                "handler_env": frame.handler_env.bindings,
                "worker_context_ref": self.evaluator._context_ref(frame.worker_context),
                "worker_context": self.evaluator._context_payload(frame.worker_context),
                "handler_context_ref": self.evaluator._context_ref(frame.handler_context),
                "handler_context": self.evaluator._context_payload(frame.handler_context),
                "outer_context_ref": self.evaluator._context_ref(frame.outer_context),
                "outer_context": self.evaluator._context_payload(frame.outer_context),
                "declaration_ref": frame.declaration_ref,
                "selection_ref": frame.selection_ref,
                "resumption_handle_ref": frame.resumption_handle_ref,
                "selection_path_ref": frame.selection_path_ref,
                "captured_continuation_ref": frame.captured_continuation_ref,
                "outer_continuation_ref": frame.outer_continuation_ref,
                "captured_continuation_control_ref": frame.captured_continuation_control_ref or captured_control_ref,
                "outer_continuation_control_ref": frame.outer_continuation_control_ref or outer_control_ref,
                "operation_result_schema_ref": frame.operation_result_schema_ref,
                "handled_result_schema_ref": frame.handled_result_schema_ref,
            }

        if isinstance(frame, ResumeReturnFrame):
            return {
                "frame": "resume-return",
                "resume_ref": frame.resume_ref,
                "selection_path_ref": frame.selection_path_ref,
                "handler_continuation_ref": frame.handler_continuation_ref,
                "handler_dynamic_tail_ref": frame.handler_dynamic_tail_ref,
                "handler_continuation_stack_ref": self.project_stack(frame.handler_continuation),
                "handler_return_frame_ref": self.project_frame(frame.handler_return_frame),
                "handler_dynamic_tail_stack_ref": self.project_stack(frame.handler_dynamic_tail),
                "handler_context_ref": self.evaluator._context_ref(frame.handler_context),
                "handler_context": self.evaluator._context_payload(frame.handler_context),
            }

        raise TypeError(f"unknown continuation frame: {frame!r}")

    def _refs_by_suffix(self, object_ref: Ref, suffix: str, seen: set[Ref] | None = None) -> set[Ref]:
        seen = seen or set()
        if object_ref in seen:
            return set()
        seen.add(object_ref)
        payload = self.store.get(object_ref)
        refs: set[Ref] = set()
        for key, value in payload.items():
            if isinstance(key, str) and key.endswith(suffix) and isinstance(value, str):
                refs.add(value)
            if isinstance(value, str) and self.store.contains(value):
                refs.update(self._refs_by_suffix(value, suffix, seen))
        return refs

    def _code_identity_refs(self, object_ref: Ref, seen: set[Ref] | None = None) -> set[Ref]:
        seen = seen or set()
        if object_ref in seen:
            return set()
        seen.add(object_ref)
        payload = self.store.get(object_ref)
        refs: set[Ref] = set()
        for key, value in payload.items():
            if key in {"binder_ref", "handler_env_def_ref", "install_def_ref"} and isinstance(value, str):
                refs.add(value)
            if isinstance(value, str) and self.store.contains(value):
                refs.update(self._code_identity_refs(value, seen))
        return refs


class _CachingRefRecursiveKernelEvaluator(RecursiveKernelEvaluator):
    """RecursiveKernelEvaluator with memoized semantic refs for spike measurements."""

    def __init__(
        self,
        program: KernelProgram,
        registry: EffectRegistry | None = None,
        event_sink: Callable[[Any], None] | None = None,
        evidence_mode: str = "trace",
    ) -> None:
        super().__init__(program, registry=registry, event_sink=event_sink, evidence_mode=evidence_mode)
        self._spike_program_ref: Ref | None = None
        self._spike_binder_refs: dict[Ref, Ref] = {}
        self._spike_handler_env_refs: dict[Ref, Ref] = {}
        self._spike_install_refs: dict[Ref, Ref] = {}
        self._spike_context_refs: dict[tuple[Ref, Ref, Ref], Ref] = {}

    def _program_ref(self) -> Ref:
        if self._spike_program_ref is None:
            self._spike_program_ref = super()._program_ref()
        return self._spike_program_ref

    def _binder_ref(self, binder_id: Ref) -> Ref:
        if binder_id not in self._spike_binder_refs:
            self._spike_binder_refs[binder_id] = super()._binder_ref(binder_id)
        return self._spike_binder_refs[binder_id]

    def _handler_env_def_ref(self, handler_env_ref: Ref) -> Ref:
        if handler_env_ref not in self._spike_handler_env_refs:
            self._spike_handler_env_refs[handler_env_ref] = super()._handler_env_def_ref(handler_env_ref)
        return self._spike_handler_env_refs[handler_env_ref]

    def _install_ref(self, install: Any) -> Ref:
        install_ref = install.install_ref
        if install_ref not in self._spike_install_refs:
            self._spike_install_refs[install_ref] = super()._install_ref(install)
        return self._spike_install_refs[install_ref]

    def _env_ref(self, env: Env) -> Ref:
        return super()._env_ref(env)

    def _context_ref(self, context: ExecutionContext) -> Ref:
        key = (context.binding_env_ref, context.region_ref, context.authority_ref)
        if key not in self._spike_context_refs:
            self._spike_context_refs[key] = super()._context_ref(context)
        return self._spike_context_refs[key]


class DagProjectingRecursiveKernelEvaluator(_CachingRefRecursiveKernelEvaluator):
    """Evaluator variant whose emitted continuation refs are DAG root refs."""

    def __init__(
        self,
        program: KernelProgram,
        registry: EffectRegistry | None = None,
        event_sink: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__(program, registry=registry, event_sink=event_sink)
        self.dag_projector = ContinuationDagProjector(self)

    @property
    def continuation_objects(self) -> Mapping[Ref, Mapping[str, Any]]:
        return self.dag_projector.store.objects

    @property
    def continuation_root_refs(self) -> tuple[Ref, ...]:
        return tuple(self.dag_projector.root_refs)

    def _kont_ref(
        self,
        kont: tuple[Frame, ...],
        *,
        continuation_kind: ContinuationImageKind,
        context: ExecutionContext,
        result_schema_ref: Ref | None = None,
    ) -> Ref:
        return self.dag_projector.project_root(
            kont.frames,
            continuation_kind=continuation_kind,
            context=context,
            result_schema_ref=result_schema_ref,
        )

    def _kont_control_ref(self, kont) -> Ref:
        return self.dag_projector.project_control_ref(kont.frames)


class ShadowDagProjectingRecursiveKernelEvaluator(_CachingRefRecursiveKernelEvaluator):
    """Evaluator variant that projects DAG refs while returning legacy refs."""

    def __init__(
        self,
        program: KernelProgram,
        registry: EffectRegistry | None = None,
        event_sink: Callable[[Any], None] | None = None,
    ) -> None:
        super().__init__(program, registry=registry, event_sink=event_sink, evidence_mode="sidecar")
        self.dag_projector = ContinuationDagProjector(self)
        self.continuation_ref_aliases: dict[Ref, Ref] = {}

    @property
    def continuation_objects(self) -> Mapping[Ref, Mapping[str, Any]]:
        return self.dag_projector.store.objects

    @property
    def continuation_root_refs(self) -> tuple[Ref, ...]:
        return tuple(self.dag_projector.root_refs)

    def _kont_ref(
        self,
        kont,
        *,
        continuation_kind: ContinuationImageKind,
        context: ExecutionContext,
        result_schema_ref: Ref | None = None,
    ) -> Ref:
        legacy_ref = super()._kont_ref(
            kont,
            continuation_kind=continuation_kind,
            context=context,
            result_schema_ref=result_schema_ref,
        )
        dag_ref = self.dag_projector.project_root(
            kont.frames,
            continuation_kind=continuation_kind,
            context=context,
            result_schema_ref=result_schema_ref,
        )
        self.continuation_ref_aliases[legacy_ref] = dag_ref
        return legacy_ref

    def _kont_control_ref(self, kont) -> Ref:
        legacy_ref = super()._kont_control_ref(kont)
        self.dag_projector.project_control_ref(kont.frames)
        return legacy_ref


def run_trace_with_dag_projection(
    program: KernelProgram,
    env: Env | None = None,
    registry: EffectRegistry | None = None,
) -> ContinuationDagTraceResult:
    records: list[TraceRecord] = []
    evaluator = DagProjectingRecursiveKernelEvaluator(
        program,
        registry=registry,
        event_sink=lambda event: records.append(record_from_event(event)),
    )
    outcome = evaluator.run(env)
    return ContinuationDagTraceResult(
        outcome=outcome,
        trace=tuple(records),
        continuation_objects=evaluator.continuation_objects,
        continuation_root_refs=evaluator.continuation_root_refs,
        program_ref=evaluator.program_ref,
        continuation_ref_aliases={},
    )


def run_trace_with_shadow_dag_projection(
    program: KernelProgram,
    env: Env | None = None,
    registry: EffectRegistry | None = None,
) -> ContinuationDagTraceResult:
    records: list[TraceRecord] = []
    evaluator = ShadowDagProjectingRecursiveKernelEvaluator(
        program,
        registry=registry,
        event_sink=lambda event: records.append(record_from_event(event)),
    )
    outcome = evaluator.run(env)
    return ContinuationDagTraceResult(
        outcome=outcome,
        trace=tuple(records),
        continuation_objects=evaluator.continuation_objects,
        continuation_root_refs=evaluator.continuation_root_refs,
        program_ref=evaluator.program_ref,
        continuation_ref_aliases=dict(evaluator.continuation_ref_aliases),
    )


def sequential_handled_effect_program(effect_count: int) -> KernelProgram:
    """Build a simple handled-effect source program for projection profiling."""

    term = Return(Var(f"y{effect_count - 1}")) if effect_count else Return(Lit(None))
    for i in reversed(range(effect_count)):
        term = Let(f"y{i}", Perform("eff.a", Lit({"i": i})), term)
    return elaborate(
        Handle(
            term,
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="continuation-dag-spike.handler.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Let("r", Resume(Lit("value")), Return(Var("r"))),
                    ),
                )
            ),
        )
    )


def profile_sequential_effects(
    effect_counts: tuple[int, ...] = (1, 5, 10, 25, 50),
) -> tuple[DagProjectionProfileRow, ...]:
    """Measure DAG object size/count for sequential handled effects."""

    rows: list[DagProjectionProfileRow] = []
    for effect_count in effect_counts:
        program = sequential_handled_effect_program(effect_count)
        start = time.perf_counter_ns()
        result = run_trace_with_dag_projection(program)
        elapsed_ms = (time.perf_counter_ns() - start) / 1_000_000
        object_sizes = tuple(_json_size(payload) for payload in result.continuation_objects.values())
        rows.append(
            DagProjectionProfileRow(
                effect_count=effect_count,
                trace_record_count=len(result.trace),
                continuation_root_count=len(result.continuation_root_refs),
                continuation_object_count=len(result.continuation_objects),
                total_object_json_bytes=sum(object_sizes),
                max_object_json_bytes=max(object_sizes, default=0),
                elapsed_ms=elapsed_ms,
            )
        )
    return tuple(rows)


def _json_size(payload: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            ensure_ascii=False,
        ).encode("utf-8")
    )
