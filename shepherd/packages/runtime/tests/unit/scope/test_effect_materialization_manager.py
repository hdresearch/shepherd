"""Focused tests for the semantic effect materialization host contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest
from shepherd_core.effects import Effect
from shepherd_core.errors import ContainmentError
from shepherd_core.scope.stream import EffectLayer
from shepherd_runtime._scope._effect_materialization import EffectMaterializationManager
from shepherd_runtime.effect_materialization import MaterializationResult


class MaterializationManagerTestEffect(Effect):
    """Simple effect for effect materialization tests."""

    effect_type: Literal["test_effect_materialization_manager"] = "test_effect_materialization_manager"


class RecordingRegistry:
    """Minimal registry stub used by the manager tests."""

    def __init__(self) -> None:
        self.materialized: list[Effect] = []

    def materialize(self, effect: Effect) -> MaterializationResult:
        self.materialized.append(effect)
        return MaterializationResult.ok(paths_affected=("/tmp/test",))

    def can_reverse(self, effect: Effect) -> bool:
        return False

    def reverse(self, effect: Effect) -> None:
        raise AssertionError("reverse() should not be called in this test")


@dataclass
class FakeEffectMaterializationScope:
    """Test host implementing the semantic effect materialization contract."""

    layers: list[EffectLayer]
    is_root: bool = True
    is_discarded: bool = False
    watermark: int = 0
    advanced_to: int | None = None
    default_registry: RecordingRegistry | None = None

    @property
    def effect_materialization_is_root(self) -> bool:
        return self.is_root

    @property
    def effect_materialization_is_discarded(self) -> bool:
        return self.is_discarded

    def effect_materialization_layers(self) -> list[EffectLayer]:
        return self.layers

    @property
    def effect_materialization_watermark(self) -> int:
        return self.watermark

    def advance_effect_materialization_watermark(self, up_to: int) -> None:
        self.watermark = up_to
        self.advanced_to = up_to

    def default_effect_materializer_registry(self) -> RecordingRegistry:
        if self.default_registry is None:
            self.default_registry = RecordingRegistry()
        return self.default_registry


def _layer(effect: Effect) -> EffectLayer:
    return EffectLayer(effect=effect, sequence=0, scope_id="scope", scope_depth=0)


class TestEffectMaterializationManager:
    """Tests that the manager uses semantic host operations only."""

    def test_uses_default_registry_from_host(self) -> None:
        effect = MaterializationManagerTestEffect()
        registry = RecordingRegistry()
        scope = FakeEffectMaterializationScope(layers=[_layer(effect)], default_registry=registry)

        summary = EffectMaterializationManager(scope).materialize()

        assert registry.materialized == [effect]
        assert summary.effects_processed == 1
        assert scope.advanced_to == 1

    def test_respects_watermark_and_advances_semantically(self) -> None:
        first = MaterializationManagerTestEffect()
        second = MaterializationManagerTestEffect()
        registry = RecordingRegistry()
        scope = FakeEffectMaterializationScope(
            layers=[_layer(first), _layer(second)],
            watermark=1,
            default_registry=registry,
        )

        summary = EffectMaterializationManager(scope).materialize()

        assert registry.materialized == [second]
        assert summary.effects_processed == 1
        assert scope.watermark == 2
        assert scope.advanced_to == 2

    def test_rejects_non_root_scope(self) -> None:
        scope = FakeEffectMaterializationScope(layers=[], is_root=False)

        with pytest.raises(RuntimeError, match="root scope"):
            EffectMaterializationManager(scope).materialize()

    def test_rejects_discarded_scope(self) -> None:
        scope = FakeEffectMaterializationScope(layers=[], is_discarded=True)

        with pytest.raises(ContainmentError, match="discarded"):
            EffectMaterializationManager(scope).materialize()
