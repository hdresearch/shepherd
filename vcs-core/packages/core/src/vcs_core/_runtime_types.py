"""Internal runtime handle and ambient-context types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vcs_core.types import ScopeInfo


@dataclass(frozen=True)
class OperationRefInfo:
    """Internal live handle for one in-flight operation ref."""

    handle_id: str
    kind: str
    ref: str
    scope_ref: str
    scope_instance_id: str
    parent_op_ref: str | None
    base_oid: str
    session_id: str | None = None
    operation_id: str | None = None
    parent_operation_id: str | None = None
    operation_label: str | None = None
    world_id: str | None = None
    world_disposition: str | None = None
    nested_parent_scope_ref: str | None = None
    nested_child_scope_ref: str | None = None
    nested_ancestry_chain: tuple[str, ...] = ()

    @property
    def durable_id(self) -> str:
        """Return the durable operation identity for this handle."""
        return self.operation_id or self.handle_id

    @property
    def display_label(self) -> str:
        """Return the best available human-readable label for this handle."""
        return self.operation_label or self.durable_id


@dataclass(frozen=True)
class ExecutionContext:
    """Explicit domain identity for one runtime execution boundary."""

    scope_ref: str
    scope_name: str
    scope_instance_id: str
    world_id: str
    session_id: str | None = None
    parent_operation_id: str | None = None

    @classmethod
    def from_scope(
        cls,
        scope: ScopeInfo,
        *,
        session_id: str | None = None,
        parent_operation_id: str | None = None,
    ) -> ExecutionContext:
        if scope.world_id is None:
            raise RuntimeError(f"Scope {scope.ref!r} is missing durable world_id.")
        return cls(
            scope_ref=scope.ref,
            scope_name=scope.name,
            scope_instance_id=scope.instance_id,
            world_id=scope.world_id,
            session_id=session_id,
            parent_operation_id=parent_operation_id,
        )

    def matches_scope(self, scope: ScopeInfo) -> bool:
        return self.scope_ref == scope.ref and self.scope_instance_id == scope.instance_id


@dataclass(frozen=True)
class RuntimeContext:
    """Framework-owned ambient runtime context."""

    world: ScopeInfo | None = None
    execution: ExecutionContext | None = None
    operation_stack: tuple[OperationRefInfo, ...] = ()

    @property
    def span(self) -> OperationRefInfo | None:
        if not self.operation_stack:
            return None
        return self.operation_stack[-1]
