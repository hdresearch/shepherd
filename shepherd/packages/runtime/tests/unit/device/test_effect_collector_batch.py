"""Tests for EffectCollector handling of ToolCallBatch effects."""

from __future__ import annotations

from shepherd_core.effects import (
    ToolCallBatch,
    ToolCallCompleted,
    ToolCallInfo,
    effect_from_dict,
)
from shepherd_runtime.device.container.effect_collector import EffectCollector


class TestToolCallBatchCausality:
    """ToolCallBatch should set _last_completed_intent_id to batch_id."""

    def test_batch_sets_intent_id(self) -> None:
        collector = EffectCollector()
        batch = ToolCallBatch(
            batch_id="batch-abc",
            tool_calls=(
                ToolCallInfo(tool_name="bash", tool_call_id="tc1"),
                ToolCallInfo(tool_name="write", tool_call_id="tc2"),
            ),
        )
        collector.emit(batch)
        assert collector.get_last_completed_intent_id() == "batch-abc"

    def test_batch_overrides_previous_completed(self) -> None:
        collector = EffectCollector()
        # First a regular completed
        collector.emit(ToolCallCompleted(tool_call_id="tc-old", tool_name="bash"))
        assert collector.get_last_completed_intent_id() == "tc-old"
        # Then a batch
        collector.emit(ToolCallBatch(batch_id="batch-new", tool_calls=()))
        assert collector.get_last_completed_intent_id() == "batch-new"

    def test_completed_overrides_batch(self) -> None:
        collector = EffectCollector()
        collector.emit(ToolCallBatch(batch_id="batch-1", tool_calls=()))
        collector.emit(ToolCallCompleted(tool_call_id="tc-after", tool_name="bash"))
        assert collector.get_last_completed_intent_id() == "tc-after"

    def test_batch_is_intent_effect(self) -> None:
        collector = EffectCollector()
        batch = ToolCallBatch(batch_id="b1", tool_calls=())
        collector.emit(batch)
        intent_effects = collector.get_intent_effects()
        assert len(intent_effects) == 1
        assert intent_effects[0].effect_type == "tool_call_batch"

    def test_batch_not_lifecycle_effect(self) -> None:
        collector = EffectCollector()
        collector.emit(ToolCallBatch(batch_id="b1", tool_calls=()))
        assert len(collector.get_lifecycle_effects()) == 0


class TestToolCallBatchSerialization:
    """ToolCallBatch must round-trip through serialize/deserialize for container transport."""

    def test_serialize_deserialize(self) -> None:
        collector = EffectCollector()
        batch = ToolCallBatch(
            batch_id="batch-xyz",
            provider_id="provider:opencode:test",
            tool_calls=(
                ToolCallInfo(
                    tool_name="bash",
                    tool_call_id="tc1",
                    input_preview="echo hello",
                    output_preview="hello",
                ),
            ),
        )
        collector.emit(batch)

        # Serialize for transport
        data = collector.serialize_for_transport()
        assert data["last_completed_intent_id"] == "batch-xyz"

        # Deserialize
        restored = EffectCollector.deserialize_from_transport(data)
        assert restored.get_last_completed_intent_id() == "batch-xyz"

        effects = restored.get_all_effects()
        assert len(effects) == 1
        assert effects[0].effect_type == "tool_call_batch"

    def test_effect_from_dict(self) -> None:
        """ToolCallBatch should be registered in EFFECT_TYPES."""
        data = {
            "effect_type": "tool_call_batch",
            "batch_id": "b1",
            "tool_calls": [
                {"tool_name": "bash", "tool_call_id": "tc1"},
            ],
        }
        effect = effect_from_dict(data)
        assert isinstance(effect, ToolCallBatch)
        assert effect.batch_id == "b1"
        assert len(effect.tool_calls) == 1
        assert effect.tool_calls[0].tool_name == "bash"
