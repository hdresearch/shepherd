from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Self

import pytest
from shepherd_core.effects import ContextMaterialized, Effect
from shepherd_core.types import ReversibilityLevel
from shepherd_runtime.materialization import (
    MaterializationIntent,
    MaterializationResult,
    clear_materialization_admission_hooks,
    clear_materializer_registry,
    register_materialization_admission_hook,
    register_materializer,
)
from shepherd_runtime.scope import Scope


@dataclass(frozen=True)
class _AdmittedContext:
    context_id: str = "admitted:test"
    reversibility: ReversibilityLevel = ReversibilityLevel.AUTO
    target_path: Path = field(default_factory=Path)
    pending: bool = True

    @property
    def has_pending_changes(self) -> bool:
        return self.pending

    def materialization_intent(self) -> MaterializationIntent:
        return MaterializationIntent(
            context_type="_AdmittedContext",
            context_id=self.context_id,
            target_path=self.target_path,
        )

    def with_materialized(self, result: MaterializationResult) -> Self:
        del result
        return replace(self, pending=False)

    def apply_effect(self, effect: Effect) -> Self:
        del effect
        return self


class _RecordingMaterializer:
    def __init__(self) -> None:
        self.calls = 0

    def materialize(self, intent: MaterializationIntent) -> MaterializationResult:
        del intent
        self.calls += 1
        return MaterializationResult.ok(paths_affected=("admitted.txt",))

    def can_rollback(self) -> bool:
        return False

    def rollback(self, intent: MaterializationIntent, result: MaterializationResult) -> None:
        del intent, result


@pytest.fixture(autouse=True)
def reset_materialization_registries() -> None:
    clear_materialization_admission_hooks()
    clear_materializer_registry()
    yield
    clear_materialization_admission_hooks()
    clear_materializer_registry()


def test_materialization_admission_hook_runs_before_materializer(tmp_path: Path) -> None:
    materializer = _RecordingMaterializer()
    register_materializer("_AdmittedContext", materializer)
    observed: list[str] = []

    def reject(intent: MaterializationIntent) -> None:
        observed.append(intent.context_id)
        raise RuntimeError("readiness blocked")

    register_materialization_admission_hook(reject)

    with Scope() as scope:
        scope.bind("workspace", _AdmittedContext(target_path=tmp_path))

        with pytest.raises(RuntimeError, match="Materialization admission failed"):
            scope.commit()

        effects = [layer.effect for layer in scope.effects if isinstance(layer.effect, ContextMaterialized)]

    assert observed == ["admitted:test"]
    assert materializer.calls == 0
    assert len(effects) == 1
    assert effects[0].success is False
    assert effects[0].error == "readiness blocked"


def test_materialization_admission_hook_allows_materializer(tmp_path: Path) -> None:
    materializer = _RecordingMaterializer()
    register_materializer("_AdmittedContext", materializer)
    register_materialization_admission_hook(lambda intent: None)

    with Scope() as scope:
        scope.bind("workspace", _AdmittedContext(target_path=tmp_path))
        result = scope.commit()

    assert materializer.calls == 1
    assert result["total_paths_affected"] == 1
