"""RecordingPipeline: infrastructure-owned single recording path.

All effect sources produce EffectRecord descriptors. The pipeline
records each one as a C1 commit via Store._emit_effect(). Substrates
never call Store._emit_effect() directly. Schema validation happens at
the VcsCore/CLI boundary before effects reach the pipeline.

VcsCore owns one RecordingPipeline instance. Built-in substrates receive
it through internal runtime binding and use it for both recording and
Store queries.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from vcs_core.store import Store
    from vcs_core.types import EffectRecord, ScopeInfo

from vcs_core._runtime_types import ExecutionContext, OperationRefInfo, RuntimeContext
from vcs_core.commons_recording import (
    CommonsShadowRecorder,
    CommonsShadowUnsupportedError,
)

_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _OpenOperationState:
    operation: OperationRefInfo
    scope: ScopeInfo
    execution: ExecutionContext
    world_id: str
    started_at: float
    logical_head_oid: str
    seq: int
    effect_count: int


@dataclass(frozen=True)
class NestedParentAuthorization:
    """Proof-carrying authorization for a cross-scope nested operation.

    Permits the active parent operation to be on a different (ancestor) scope
    — the seam for nested sub-task execution, gated by
    VCS_CORE_NESTED_OPERATIONS. Constructed only by the coordinator's
    ancestry walk (``_vcscore_runtime._nested_parent_authorization``), it
    names the exact (parent-op scope, child scope) pair plus the ancestry
    chain that proved it; ``begin_operation`` re-checks the pair against the
    *live* parent operation, so a stale, wrong-scope, or hand-rolled
    authorization cannot defeat the same-scope guard. This discharges the
    graduation gate of ``nested-operations.md`` (the former raw
    ``allow_nested_parent: bool`` is gone — a bare ``True`` is no longer
    expressible).
    """

    parent_scope_ref: str
    child_scope_ref: str
    #: Scope refs walked child-parent-upward, ending at (and including)
    #: ``parent_scope_ref`` — the proof the walk found ancestry.
    ancestry_chain: tuple[str, ...]

    def admits(self, *, parent_scope_ref: str, child_scope_ref: str) -> bool:
        """Whether this authorization covers the live (parent, child) pair."""
        return (
            self.parent_scope_ref == parent_scope_ref
            and self.child_scope_ref == child_scope_ref
            and parent_scope_ref in self.ancestry_chain
        )


@dataclass(frozen=True)
class CommonsShadowDiagnostic:
    """Commons projection failure captured before re-raising to the caller."""

    carrier_oid: str
    scope_ref: str
    scope_name: str
    error_type: str
    message: str


class RecordingPipeline:
    """Infrastructure-owned recording path.

    Wraps Store._emit_effect() with runtime-context tracking. Coordinator
    paths install an explicit execution context for mutation boundaries.
    Current world identity is exposed through ``context.world`` and
    ``execution_context``.
    """

    def __init__(self, store: Store, *, commons_shadow: bool | None = None) -> None:
        self._store = store
        self._context = RuntimeContext()
        self._runtime_effect_recorder: Callable[..., list[str]] | None = None
        self._operation_state_stack: tuple[_OpenOperationState, ...] = ()
        self._commons_shadow_enabled = _commons_shadow_enabled() if commons_shadow is None else commons_shadow
        self._commons_shadow_recorder: CommonsShadowRecorder | None = None
        self._commons_shadow_diagnostics: tuple[CommonsShadowDiagnostic, ...] = ()

    @property
    def store(self) -> Store:
        """Read-only Store access for substrate queries."""
        return self._store

    @property
    def context(self) -> RuntimeContext:
        """Current ambient runtime context."""
        return self._context

    @property
    def execution_context(self) -> ExecutionContext | None:
        """Current explicit execution context, if one is installed."""
        return self._context.execution

    @property
    def operation(self) -> OperationRefInfo | None:
        """Current active operation span, if any."""
        return self._context.span

    @property
    def commons_shadow_diagnostics(self) -> tuple[CommonsShadowDiagnostic, ...]:
        """Commons projection failures observed while recording Store commits."""
        return self._commons_shadow_diagnostics

    def set_context(self, context: RuntimeContext) -> None:
        """Replace the full ambient runtime context."""
        if not context.operation_stack:
            self._operation_state_stack = ()
        elif tuple(state.operation for state in self._operation_state_stack) != context.operation_stack:
            msg = "set_context() with a non-empty operation stack requires matching runtime operation state."
            raise RuntimeError(msg)
        self._context = self._validate_context(context)

    def set_execution_context(
        self,
        scope: ScopeInfo,
        *,
        session_id: str | None = None,
        parent_operation_id: str | None = None,
    ) -> None:
        """Install explicit runtime identity for work in one world."""
        self._set_world_and_execution(
            scope,
            ExecutionContext.from_scope(
                scope,
                session_id=session_id,
                parent_operation_id=parent_operation_id,
            ),
        )

    def clear_execution_context(self) -> None:
        """Clear the active runtime execution identity."""
        self._set_world_and_execution(None, None)

    def restore_execution_context(self, context: RuntimeContext) -> None:
        """Restore only execution identity, preserving any open operation stack."""
        self._set_world_and_execution(context.world, context.execution)

    def set_scope(self, scope: ScopeInfo | None) -> None:
        """Set the current active world.

        Compatibility shim for standalone pipeline tests and older internal
        call sites. Coordinator-owned runtime paths should prefer
        set_execution_context() so the ambient world has explicit identity.
        """
        execution = ExecutionContext.from_scope(scope) if scope is not None else None
        self._set_world_and_execution(scope, execution)

    def _set_world_and_execution(
        self,
        scope: ScopeInfo | None,
        execution: ExecutionContext | None,
    ) -> None:
        if self._context.span is not None:
            current_scope = self._context.world
            same_scope = (
                scope is not None
                and current_scope is not None
                and scope.ref == current_scope.ref
                and scope.instance_id == current_scope.instance_id
            )
            if not same_scope:
                msg = "Cannot switch or clear the active scope while an operation span is open. Call reset() for teardown."
                raise RuntimeError(msg)
        if scope is None and execution is not None:
            msg = "Execution context requires a world."
            raise RuntimeError(msg)
        if scope is not None and execution is not None and not execution.matches_scope(scope):
            msg = "Execution context does not match active world."
            raise RuntimeError(msg)
        self._context = self._validate_context(replace(self._context, world=scope, execution=execution))

    @staticmethod
    def _validate_context(context: RuntimeContext) -> RuntimeContext:
        if context.world is None:
            if context.execution is not None:
                msg = "RuntimeContext cannot carry execution identity without a world."
                raise RuntimeError(msg)
            if context.operation_stack:
                msg = "RuntimeContext cannot carry operation state without a world."
                raise RuntimeError(msg)
            return context
        if context.execution is None:
            msg = "RuntimeContext requires execution identity for an active world."
            raise RuntimeError(msg)
        if not context.execution.matches_scope(context.world):
            msg = "RuntimeContext execution identity does not match its world."
            raise RuntimeError(msg)
        operation = context.span
        if operation is None:
            return context
        if operation.scope_ref != context.world.ref or operation.scope_instance_id != context.world.instance_id:
            msg = "RuntimeContext operation identity does not match its world."
            raise RuntimeError(msg)
        return context

    def _execution_for_operation(self, scope: ScopeInfo, *, session_id: str | None) -> ExecutionContext:
        parent_operation = self.operation
        parent_operation_id = parent_operation.durable_id if parent_operation is not None else None
        if (
            session_id is None
            and parent_operation_id is None
            and self._context.execution is not None
            and self._context.execution.matches_scope(scope)
        ):
            return self._context.execution
        return ExecutionContext.from_scope(
            scope,
            session_id=session_id,
            parent_operation_id=parent_operation_id,
        )

    def reset(self) -> None:
        """Clear all ambient runtime state during teardown or recovery."""
        self._context = RuntimeContext()
        self._operation_state_stack = ()

    def set_runtime_effect_recorder(
        self,
        recorder: Callable[..., list[str]] | None,
    ) -> None:
        """Install a coordinator-owned runtime recording hook."""
        self._runtime_effect_recorder = recorder

    def resolve_world(self, scope: ScopeInfo | None = None) -> ScopeInfo | None:
        """Resolve an explicit world or fall back to ambient runtime context."""
        return scope if scope is not None else self._context.world

    def require_world(self, scope: ScopeInfo | None = None) -> ScopeInfo:
        """Resolve the effective world or raise if none is available."""
        effective_scope = self.resolve_world(scope)
        if effective_scope is None:
            msg = "No execution context. Establish a runtime context or pass scope=."
            raise RuntimeError(msg)
        return effective_scope

    def current_write_ref(self, scope: ScopeInfo | None = None) -> str:
        """Return the ref that should receive new commits for the effective world."""
        effective_scope = self.require_world(scope)
        operation = self.operation
        if operation is not None and operation.scope_ref == effective_scope.ref:
            return operation.ref
        return effective_scope.ref

    def current_operation(self) -> OperationRefInfo | None:
        """Return the current active operation span, if any."""
        return self.operation

    def _current_operation_state(self) -> _OpenOperationState | None:
        if not self._operation_state_stack:
            return None
        return self._operation_state_stack[-1]

    def _context_after_operation_pop(
        self,
        *,
        operation_stack: tuple[OperationRefInfo, ...],
        state_stack: tuple[_OpenOperationState, ...],
    ) -> RuntimeContext:
        if len(operation_stack) != len(state_stack):
            raise RuntimeError("Runtime operation stack is out of sync with operation state.")
        if not operation_stack:
            return self._validate_context(replace(self._context, operation_stack=operation_stack))
        enclosing = state_stack[-1]
        return self._validate_context(
            replace(
                self._context,
                world=enclosing.scope,
                execution=enclosing.execution,
                operation_stack=operation_stack,
            )
        )

    @staticmethod
    def _world_id(scope: ScopeInfo) -> str:
        if scope.world_id is None:
            raise RuntimeError(f"Scope {scope.ref!r} is missing durable world_id.")
        return scope.world_id

    def begin_operation(
        self,
        *,
        handle_id: str,
        kind: str,
        operation_id: str | None = None,
        operation_label: str | None = None,
        session_id: str | None = None,
        metadata: dict[str, object] | None = None,
        scope: ScopeInfo | None = None,
        nested_parent: NestedParentAuthorization | None = None,
        world_disposition: str | None = None,
    ) -> OperationRefInfo:
        """Open an operation span for the effective world.

        ``nested_parent`` (experimental, default None) is the proof-carrying
        authorization for the active parent operation to be on a different
        (ancestor) scope — the seam for nested sub-task execution, gated by
        the caller behind VCS_CORE_NESTED_OPERATIONS. The authorization is
        re-checked here against the live parent operation (a wrong-pair or
        stale authorization is rejected); default None keeps the same-scope
        invariant. This discharged the graduation gate of
        docs/engineering/convergence/nested-operations.md (2026-06-10): the
        former ``allow_nested_parent: bool`` escape hatch is gone.
        """
        effective_scope = self.require_world(scope)
        if self._commons_shadow_enabled:
            raise CommonsShadowUnsupportedError(
                "commons shadow recording does not support operation spans yet; "
                "disable VCS_CORE_COMMONS_SHADOW or use a pipeline without commons_shadow=True"
            )
        parent_operation = self.operation
        if parent_operation is not None and parent_operation.scope_ref != effective_scope.ref:
            authorized = nested_parent is not None and nested_parent.admits(
                parent_scope_ref=parent_operation.scope_ref,
                child_scope_ref=effective_scope.ref,
            )
            if not authorized:
                msg = (
                    f"Active operation handle {parent_operation.handle_id!r} belongs to "
                    f"{parent_operation.scope_ref}, not {effective_scope.ref}."
                )
                raise RuntimeError(msg)
        started_at = time.time()
        provisional_operation = OperationRefInfo(
            handle_id=handle_id,
            kind=kind,
            ref="",
            scope_ref=effective_scope.ref,
            scope_instance_id=effective_scope.instance_id,
            parent_op_ref=parent_operation.ref if parent_operation is not None else None,
            base_oid="",
            session_id=session_id,
            operation_id=operation_id or handle_id,
            parent_operation_id=(parent_operation.durable_id if parent_operation is not None else None),
            operation_label=operation_label or handle_id,
            world_id=self._world_id(effective_scope),
            world_disposition=world_disposition,
            nested_parent_scope_ref=None if nested_parent is None else nested_parent.parent_scope_ref,
            nested_child_scope_ref=None if nested_parent is None else nested_parent.child_scope_ref,
            nested_ancestry_chain=() if nested_parent is None else nested_parent.ancestry_chain,
        )
        operation = self._store.begin_operation(
            effective_scope.ref,
            handle_id=handle_id,
            kind=kind,
            world_id=self._world_id(effective_scope),
            scope_instance_id=effective_scope.instance_id,
            parent_op_ref=parent_operation.ref if parent_operation is not None else None,
            operation_id=provisional_operation.durable_id,
            operation_label=provisional_operation.display_label,
            session_id=session_id,
            metadata=dict(metadata or {}),
            world_disposition=world_disposition,
            nested_parent_scope_ref=None if nested_parent is None else nested_parent.parent_scope_ref,
            nested_child_scope_ref=None if nested_parent is None else nested_parent.child_scope_ref,
            nested_ancestry_chain=() if nested_parent is None else nested_parent.ancestry_chain,
        )
        start_oid = self._store.log(ref=operation.ref, max_count=1)[0].oid
        execution = self._execution_for_operation(effective_scope, session_id=session_id)
        self._context = replace(
            self._context,
            world=effective_scope,
            execution=execution,
            operation_stack=(*self._context.operation_stack, operation),
        )
        self._context = self._validate_context(self._context)
        self._operation_state_stack = (
            *self._operation_state_stack,
            _OpenOperationState(
                operation=operation,
                scope=effective_scope,
                execution=execution,
                world_id=self._world_id(effective_scope),
                started_at=started_at,
                logical_head_oid=start_oid,
                seq=0,
                effect_count=0,
            ),
        )
        return operation

    def end_operation(
        self,
        *,
        handle_id: str | None = None,
        metadata: dict[str, object] | None = None,
        status: str = "ok",
        scope: ScopeInfo | None = None,
    ) -> str:
        """Finalize the active operation into its parent or world ref."""
        operation = self.operation
        if operation is None:
            raise RuntimeError("No active operation.")
        if handle_id is not None and handle_id != operation.handle_id:
            raise RuntimeError(f"Active operation handle is {operation.handle_id!r}, not {handle_id!r}.")
        target_scope = scope if scope is not None else self._context.world
        state = self._current_operation_state()
        if state is None or state.operation.ref != operation.ref:
            raise RuntimeError("Runtime operation stack is out of sync with the active operation.")
        completion_metadata = dict(metadata or {})
        tip_oid = self._store.finalize_operation(
            operation,
            scope=target_scope if operation.parent_op_ref is None else None,
            metadata=completion_metadata,
            status=status,
        )
        operation_stack = self._context.operation_stack[:-1]
        state_stack = self._operation_state_stack[:-1]
        self._context = self._context_after_operation_pop(operation_stack=operation_stack, state_stack=state_stack)
        self._operation_state_stack = state_stack
        return tip_oid

    def abort_operation(
        self,
        *,
        handle_id: str | None = None,
        metadata: dict[str, object] | None = None,
        status: str = "error",
    ) -> str:
        """Abort the active operation and restore the enclosing span context."""
        operation = self.operation
        if operation is None:
            raise RuntimeError("No active operation.")
        if handle_id is not None and handle_id != operation.handle_id:
            raise RuntimeError(f"Active operation handle is {operation.handle_id!r}, not {handle_id!r}.")
        state = self._current_operation_state()
        if state is None or state.operation.ref != operation.ref:
            raise RuntimeError("Runtime operation stack is out of sync with the active operation.")
        archive_ref = self._store.abort_operation(
            operation,
            metadata=dict(metadata or {}),
            status=status,
        )
        operation_stack = self._context.operation_stack[:-1]
        state_stack = self._operation_state_stack[:-1]
        self._context = self._context_after_operation_pop(operation_stack=operation_stack, state_stack=state_stack)
        self._operation_state_stack = state_stack
        return archive_ref

    def _stamp_framework_metadata(self, effect: EffectRecord, *, scope: ScopeInfo) -> dict[str, object]:
        """Attach framework-owned identity metadata to one effect.

        Substrates own effect payload metadata. The framework owns the
        ambient world identity under which the effect is recorded.
        """
        return {
            **effect.metadata,
            "world_id": self._world_id(scope),
            "scope_instance_id": scope.instance_id,
        }

    def _record_commons_shadow(self, scope: ScopeInfo, carrier_oid: str) -> None:
        if not self._commons_shadow_enabled:
            return
        if self._commons_shadow_recorder is None:
            self._commons_shadow_recorder = CommonsShadowRecorder(self._store)
        try:
            self._commons_shadow_recorder.record_carrier_commit(scope, carrier_oid)
        except Exception as exc:
            diagnostic = CommonsShadowDiagnostic(
                carrier_oid=carrier_oid,
                scope_ref=scope.ref,
                scope_name=scope.name,
                error_type=type(exc).__name__,
                message=str(exc),
            )
            self._commons_shadow_diagnostics = (*self._commons_shadow_diagnostics, diagnostic)
            _LOG.warning(
                "commons shadow projection failed for Store commit %s in scope %s; refusing successful publication",
                carrier_oid,
                scope.ref,
                exc_info=True,
            )
            raise

    def record(
        self,
        effects: Sequence[EffectRecord],
        *,
        substrate: str,
        scope: ScopeInfo | None = None,
    ) -> list[str]:
        """Record EffectRecords as C1 commits. Returns list of OIDs."""
        effective_scope = self.require_world(scope)
        oids: list[str] = []
        operation = self.operation
        for effect in effects:
            active_operation = (
                operation if operation is not None and operation.scope_ref == effective_scope.ref else None
            )
            if active_operation is not None:
                state = self._current_operation_state()
                if state is None or state.operation.ref != active_operation.ref:
                    raise RuntimeError("Runtime operation stack is out of sync with the active operation.")
                oid = self._store.append_operation_effect(
                    active_operation,
                    effect.effect_type,
                    dict(effect.metadata),
                    workspace_changes=list(effect.workspace_changes) or None,
                    substrate=substrate,
                )
                self._operation_state_stack = (
                    *self._operation_state_stack[:-1],
                    replace(
                        state,
                        logical_head_oid=oid,
                        seq=state.seq + 1,
                        effect_count=state.effect_count + 1,
                    ),
                )
            else:
                stamped_metadata = self._stamp_framework_metadata(effect, scope=effective_scope)
                oid = self._store._emit_effect(
                    effective_scope,
                    effect.effect_type,
                    stamped_metadata,
                    workspace_changes=list(effect.workspace_changes) or None,
                    substrate=substrate,
                )
            if active_operation is None:
                self._record_commons_shadow(effective_scope, oid)
            oids.append(oid)
        return oids

    def record_runtime_effects(
        self,
        effects: Sequence[EffectRecord],
        *,
        substrate: str,
        scope: ScopeInfo | None = None,
        boundary_policy: str = "append_or_root",
        operation_id: str | None = None,
        operation_kind: str | None = None,
        operation_label: str | None = None,
        operation_metadata: dict[str, object] | None = None,
        workspace_driver_command: str | None = None,
    ) -> list[str]:
        """Record runtime-originated effects through the coordinator hook.

        When VcsCore owns this pipeline, runtime recording delegates to a
        coordinator-installed hook so write-policy decisions stay
        centralized. Standalone pipelines fall back to direct recording.
        """
        if self._runtime_effect_recorder is not None:
            return self._runtime_effect_recorder(
                effects,
                substrate=substrate,
                scope=scope,
                boundary_policy=boundary_policy,
                operation_id=operation_id,
                operation_kind=operation_kind,
                operation_label=operation_label,
                operation_metadata=operation_metadata,
                workspace_driver_command=workspace_driver_command,
            )
        return self.record(effects, substrate=substrate, scope=scope)

    def record_one(
        self,
        effect: EffectRecord,
        *,
        substrate: str,
        scope: ScopeInfo | None = None,
    ) -> str:
        """Convenience: record a single effect. Returns OID."""
        return self.record([effect], substrate=substrate, scope=scope)[0]

    def record_runtime_effect(
        self,
        effect: EffectRecord,
        *,
        substrate: str,
        scope: ScopeInfo | None = None,
        boundary_policy: str = "append_or_root",
        operation_id: str | None = None,
        operation_kind: str | None = None,
        operation_label: str | None = None,
        operation_metadata: dict[str, object] | None = None,
        workspace_driver_command: str | None = None,
    ) -> str:
        """Convenience wrapper for one runtime-originated effect."""
        return self.record_runtime_effects(
            [effect],
            substrate=substrate,
            scope=scope,
            boundary_policy=boundary_policy,
            operation_id=operation_id,
            operation_kind=operation_kind,
            operation_label=operation_label,
            operation_metadata=operation_metadata,
            workspace_driver_command=workspace_driver_command,
        )[0]


def _commons_shadow_enabled() -> bool:
    return os.environ.get("VCS_CORE_COMMONS_SHADOW", "").strip().lower() in _TRUE_ENV_VALUES
