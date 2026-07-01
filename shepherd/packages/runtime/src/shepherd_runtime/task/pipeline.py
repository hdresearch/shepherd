"""Runtime-owned pipeline task primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

from pydantic import BaseModel


class OnErrorPolicy:
    """Base class for stage error policies. Not instantiated directly."""


class _FatalPolicy(OnErrorPolicy):
    """Propagate the exception. Pipeline stops. StageFailed emitted."""

    def __repr__(self) -> str:
        return "OnError.fatal"


class _SkipPolicy(OnErrorPolicy):
    """Stage treated as not run. run_stage returns None. StageSkipped emitted."""

    def __repr__(self) -> str:
        return "OnError.skip"


@dataclass(frozen=True)
class _DefaultPolicy(OnErrorPolicy):
    """Replace with static default values. StageCompleted(defaulted=True) emitted."""

    values: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"OnError.default({', '.join(f'{k}={v!r}' for k, v in self.values.items())})"


@dataclass(frozen=True)
class _ContinueWithPolicy(OnErrorPolicy):
    """Continue with fallback values. StageCompleted(partial=True) emitted."""

    values: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"OnError.continue_with({', '.join(f'{k}={v!r}' for k, v in self.values.items())})"


class OnError:
    """Sealed type with four variants for per-stage error handling."""

    fatal: ClassVar[OnErrorPolicy] = _FatalPolicy()
    skip: ClassVar[OnErrorPolicy] = _SkipPolicy()

    @staticmethod
    def default(**values: Any) -> OnErrorPolicy:
        return _DefaultPolicy(values=values)

    @staticmethod
    def continue_with(**values: Any) -> OnErrorPolicy:
        return _ContinueWithPolicy(values=values)


def _make_stage_stub(stage_name: str, **values: Any) -> BaseModel:
    """Create a dynamic Pydantic BaseModel instance from keyword arguments."""
    annotations = {k: (type(v) if v is not None else Any) for k, v in values.items()}
    model_cls = type(
        f"StageStub_{stage_name}",
        (BaseModel,),
        {"__annotations__": annotations, **values},
    )
    return model_cls(**values)  # type: ignore[no-any-return]


# =============================================================================
# Stage Descriptor for Parallel Execution
# =============================================================================


@dataclass(frozen=True)
class Stage:
    """Descriptor for a stage to execute in run_stages_parallel().

    Bundles the stage name, task class, inputs, and optional per-stage
    error policy into a single self-documenting object.

    Example::

        await self.run_stages_parallel(
            Stage("doc_gaps", AnalyzeCode, {"concern": "docs", "diff": diff}),
            Stage("correctness", AnalyzeCode, {"concern": "correctness", "diff": diff}),
            max_concurrency=2,
        )
    """

    name: str
    task_class: type | Callable[..., Any]
    inputs: dict[str, Any] = field(default_factory=dict)
    on_error: OnErrorPolicy = field(default_factory=lambda: _SkipPolicy())


__all__ = ["OnError", "OnErrorPolicy", "Stage"]
