"""Runtime context mixins and protocols owned by `shepherd-runtime`."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, Self, runtime_checkable

from shepherd_core.context.kernel import ExecutionContext, ExecutionContextDefaults

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shepherd_runtime.device.transfer import TransferBundle
    from shepherd_runtime.scope_types import BindScope, ContextRef, TransferScope


@runtime_checkable
class Sandbox(Protocol):
    """Protocol for isolated execution sandboxes."""

    @property
    def path(self) -> Path:
        """Root path of sandbox filesystem."""
        ...

    def git_diff(self) -> str:
        """Get unified diff of all changes made in sandbox."""
        ...

    def changed_files(self) -> Sequence[str]:
        """List files changed in sandbox."""
        ...

    def setup(self, context: ExecutionContext) -> None:
        """Set up the sandbox with the given context."""
        ...

    def discard(self) -> None:
        """Discard sandbox and cleanup all resources."""
        ...


class NullSandbox:
    """Sandbox for contexts without filesystem isolation."""

    @property
    def path(self) -> Path:
        return Path("/dev/null")

    def git_diff(self) -> str:
        return ""

    def changed_files(self) -> Sequence[str]:
        return []

    def setup(self, context: ExecutionContext) -> None:
        return None

    def discard(self) -> None:
        return None


class RuntimeContextDefaults(ExecutionContextDefaults):
    """Runtime-only default hooks layered on the kernel lifecycle defaults."""

    def to_state(self) -> Any:
        return None

    @classmethod
    def from_state(cls, state: Any, sandbox_path: Path | None = None) -> Self:
        raise NotImplementedError(
            f"{cls.__name__} does not support reconstruction from state. "
            f"Override from_state() to enable device boundary crossing."
        )

    def transfer_bundle(self, scope: TransferScope) -> TransferBundle | None:
        return None


class Bindable:
    """Mixin that adds `.bind()` for fluent scope binding."""

    __binding_name__: ClassVar[str | None] = None

    def bind(
        self,
        scope: BindScope,
        name: str | None = None,
    ) -> ContextRef[Self]:  # type: ignore[type-var]
        binding_name = name if name is not None else self.__binding_name__
        if binding_name is None:
            raise ValueError(
                f"{type(self).__name__} has no default binding name. "
                f"Either set __binding_name__ on the class or provide name explicitly."
            )
        return scope.bind(binding_name, self)  # type: ignore[call-overload, no-any-return]


class BindableContext(RuntimeContextDefaults, Bindable):
    """Convenience base combining runtime defaults + bindable scope helpers."""

    @classmethod
    def requires_sandbox(cls) -> bool:
        return False


__all__ = [
    "Bindable",
    "BindableContext",
    "NullSandbox",
    "RuntimeContextDefaults",
    "Sandbox",
]
