"""Projection shape regression spike.

Promoted from the 2026-05-23 §2330 capability spike that pressure-tested the
eight-pass canonicalization algorithm across five `-lite`-relevant program
shapes (pure let, Resume-shape, Abort-shape, nested-handler abandoned,
nested-handler resumed) before commit #72 landed.

Per `260523-2330-projection-shape-spike-findings.md` §"What Was Confirmed":
all five programs produce byte-identical canonical ref maps across three
independent runs. This module keeps the drift guard CI-tracked now that the
algorithm is the production projection in
`shepherd_kernel_v3_reference.projection`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from shepherd_kernel_v3_reference.kernel import elaborate
from shepherd_kernel_v3_reference.kernel.program_admission import ensure_prepared_kernel_program
from shepherd_kernel_v3_reference.kernel.replay import KernelReplaySession
from shepherd_kernel_v3_reference.projection import (
    semantic_batch_from_transition,
    validate_semantic_batch,
)
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.semantic import SemanticTransitionBatch
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import (
    Abort,
    Computation,
    Handle,
    Let,
    Lit,
    Perform,
    Resume,
    Return,
    Var,
)


@dataclass(frozen=True)
class ProjectionShapeResult:
    """Single-program spike measurement."""

    program_name: str
    trace_record_count: int
    ref_map_entry_count: int
    canonical_ref_values: tuple[str, ...]
    byte_stable_across_runs: bool


def _pure_let() -> Computation:
    return Let("x", Return(Lit(1)), Return(Var("x")))


def _resume_shape() -> Computation:
    return Handle(
        Let("y", Perform("ask", Lit(None)), Return(Var("y"))),
        HandlerEnv((
            StaticHandlerInstall(
                effect_kind="ask",
                handler_id="ask.v1",
                handled_result_schema=AnySchema(),
                payload_name="_payload",
                body=Let("r", Resume(Lit(42)), Return(Var("r"))),
            ),
        )),
    )


def _abort_shape() -> Computation:
    return Handle(
        Perform("ask", Lit(None)),
        HandlerEnv((
            StaticHandlerInstall(
                effect_kind="ask",
                handler_id="ask.v1",
                handled_result_schema=AnySchema(),
                payload_name="_payload",
                body=Abort(Lit(0)),
            ),
        )),
    )


def _nested_abandoned() -> Computation:
    """Inner perform then outer perform; outer aborts. Inner selection is
    skipped/abandoned. Mirrors 2330 spike P4."""

    inner = StaticHandlerInstall(
        effect_kind="inner",
        handler_id="inner.v1",
        handled_result_schema=AnySchema(),
        payload_name="_payload",
        body=Let("r", Resume(Lit(7)), Return(Var("r"))),
    )
    outer = StaticHandlerInstall(
        effect_kind="outer",
        handler_id="outer.v1",
        handled_result_schema=AnySchema(),
        payload_name="_payload",
        body=Abort(Lit(99)),
    )
    return Handle(
        Handle(
            Let("a", Perform("inner", Lit(None)),
                Let("b", Perform("outer", Lit(None)), Return(Var("a")))),
            HandlerEnv((inner,)),
        ),
        HandlerEnv((outer,)),
    )


def _nested_resumed() -> Computation:
    """Inner resumes; outer resumes. Mirrors 2330 spike P5."""

    inner = StaticHandlerInstall(
        effect_kind="inner",
        handler_id="inner.v1",
        handled_result_schema=AnySchema(),
        payload_name="_payload",
        body=Let("r", Resume(Lit(7)), Return(Var("r"))),
    )
    outer = StaticHandlerInstall(
        effect_kind="outer",
        handler_id="outer.v1",
        handled_result_schema=AnySchema(),
        payload_name="_payload",
        body=Let("r", Resume(Lit(3)), Return(Var("r"))),
    )
    return Handle(
        Handle(
            Let("a", Perform("inner", Lit(None)),
                Let("b", Perform("outer", Lit(None)), Return(Var("a")))),
            HandlerEnv((inner,)),
        ),
        HandlerEnv((outer,)),
    )


PROGRAMS: Mapping[str, Callable[[], Computation]] = {
    "P1_pure_let": _pure_let,
    "P2_resume_shape": _resume_shape,
    "P3_abort_shape": _abort_shape,
    "P4_nested_abandoned": _nested_abandoned,
    "P5_nested_resumed": _nested_resumed,
}


def project_program(program: Computation) -> SemanticTransitionBatch:
    """Run a program through the session path and project to a batch."""

    prepared = ensure_prepared_kernel_program(elaborate(program))
    session, transition = KernelReplaySession.start(prepared)
    catalog = dict(session._evaluator.continuation_objects)
    batch = semantic_batch_from_transition(transition, session.state, catalog)
    if not isinstance(batch, SemanticTransitionBatch):
        raise AssertionError("projection produced ProfileRejected unexpectedly")
    validate_semantic_batch(batch)
    return batch


def run_shape_spike(*, runs: int = 3) -> tuple[ProjectionShapeResult, ...]:
    """Project every corpus program `runs` times and verify byte stability."""

    results = []
    for name, builder in PROGRAMS.items():
        prior_entries: tuple[tuple[str, str], ...] | None = None
        prior_records: tuple = ()
        byte_stable = True
        first_batch: SemanticTransitionBatch | None = None
        for _ in range(runs):
            batch = project_program(builder())
            if first_batch is None:
                first_batch = batch
                prior_entries = batch.ref_map.entries
                prior_records = batch.records
            elif batch.ref_map.entries != prior_entries or batch.records != prior_records:
                byte_stable = False
        assert first_batch is not None
        results.append(
            ProjectionShapeResult(
                program_name=name,
                trace_record_count=len(first_batch.records),
                ref_map_entry_count=len(first_batch.ref_map.entries),
                canonical_ref_values=tuple(
                    canonical for _runtime, canonical in first_batch.ref_map.entries
                ),
                byte_stable_across_runs=byte_stable,
            )
        )
    return tuple(results)


__all__ = ["PROGRAMS", "ProjectionShapeResult", "project_program", "run_shape_spike"]
