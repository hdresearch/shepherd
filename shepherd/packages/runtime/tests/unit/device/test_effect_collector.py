"""Unit tests for EffectCollector.

Tests the minimal scope implementation for container execution,
validating effect collection, intent tracking, classification,
and serialization for transport across container boundaries.

Promoted from: scripts/spike_effect_collector.py
"""

import json
from typing import Literal

from shepherd_banking.contexts.effects import TransferInitiated
from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import (
    KERNEL_EFFECT_REGISTRY,
    AgentMessage,
    Effect,
    FileCreate,
    PromptSent,
    TaskCompleted,
    TaskStarted,
    ToolCallCompleted,
    ToolCallRejected,
    ToolCallStarted,
)
from shepherd_runtime.device.container.effect_collector import EffectCollector
from shepherd_runtime.effects import compose_effect_registry

# =============================================================================
# Protocol Compliance Tests
# =============================================================================


class TestProtocolCompliance:
    """Tests for ExecutionContextProtocol compliance."""

    def test_has_id_property(self):
        """EffectCollector has required 'id' property."""
        collector = EffectCollector()
        assert hasattr(collector, "id")
        assert isinstance(collector.id, str)

    def test_has_emit_method(self):
        """EffectCollector has required 'emit' method."""
        collector = EffectCollector()
        assert hasattr(collector, "emit")
        assert callable(collector.emit)

    def test_id_returns_configured_value(self):
        """ID property returns the configured identifier."""
        collector = EffectCollector(_id="custom-id")
        assert collector.id == "custom-id"

    def test_default_id(self):
        """Default ID is 'container-collector'."""
        collector = EffectCollector()
        assert collector.id == "container-collector"

    def test_protocol_isinstance_check(self):
        """EffectCollector passes isinstance check for protocol."""
        from shepherd_core.foundation.protocols.device import ExecutionContextProtocol

        collector = EffectCollector()
        assert isinstance(collector, ExecutionContextProtocol)

    def test_intent_effect_types_constant(self):
        """_INTENT_EFFECT_TYPES contains correct effect type strings."""
        from shepherd_runtime.device.container.effect_collector import _INTENT_EFFECT_TYPES

        # Verify the constant contains the expected effect types
        assert "tool_call_started" in _INTENT_EFFECT_TYPES
        assert "tool_call_completed" in _INTENT_EFFECT_TYPES
        assert "tool_call_rejected" in _INTENT_EFFECT_TYPES
        assert "tool_call_batch" in _INTENT_EFFECT_TYPES

        # Verify it only contains these four types
        assert len(_INTENT_EFFECT_TYPES) == 4

        # Verify it matches the effect types from actual effect classes
        assert ToolCallStarted().effect_type in _INTENT_EFFECT_TYPES
        assert (
            ToolCallCompleted(tool_call_id="test", tool_name="test", success=True).effect_type in _INTENT_EFFECT_TYPES
        )
        assert (
            ToolCallRejected(tool_call_id="test", tool_name="test", reason="test").effect_type in _INTENT_EFFECT_TYPES
        )


# =============================================================================
# Effect Collection Tests
# =============================================================================


class TestEffectCollection:
    """Tests for effect emission and collection."""

    def test_emit_single_effect(self):
        """Emitting a single effect adds it to collection."""
        collector = EffectCollector()
        effect = TaskStarted(task_name="test")

        collector.emit(effect)

        assert len(collector) == 1
        assert collector.get_all_effects()[0] == effect

    def test_emission_order_preserved(self):
        """Effects are collected in emission order."""
        collector = EffectCollector(_id="test-order")

        collector.emit(TaskStarted(task_name="my_task"))
        collector.emit(PromptSent(task_name="my_task", user_prompt="Hello"))
        collector.emit(ToolCallStarted(tool_call_id="call_1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="call_1", tool_name="bash", success=True))
        collector.emit(AgentMessage(content="Done"))
        collector.emit(TaskCompleted(task_name="my_task"))

        effects = collector.get_all_effects()

        assert len(effects) == 6
        assert effects[0].effect_type == "task_started"
        assert effects[1].effect_type == "prompt_sent"
        assert effects[2].effect_type == "tool_call_started"
        assert effects[3].effect_type == "tool_call_completed"
        assert effects[4].effect_type == "agent_message"
        assert effects[5].effect_type == "task_completed"

    def test_empty_collector(self):
        """Empty collector returns empty tuples."""
        collector = EffectCollector(_id="empty")

        assert len(collector) == 0
        assert collector.get_all_effects() == ()
        assert collector.get_intent_effects() == ()
        assert collector.get_lifecycle_effects() == ()

    def test_clear_removes_all_effects(self):
        """Clear method removes all collected effects."""
        collector = EffectCollector()
        collector.emit(TaskStarted(task_name="test"))
        collector.emit(ToolCallStarted(tool_call_id="c1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="c1", tool_name="bash", success=True))

        assert len(collector) == 3

        collector.clear()

        assert len(collector) == 0
        assert collector.get_last_completed_intent_id() is None


# =============================================================================
# Intent Tracking Tests
# =============================================================================


class TestIntentTracking:
    """Tests for last completed intent tracking."""

    def test_initial_state_is_none(self):
        """Initially no intent is tracked."""
        collector = EffectCollector()
        assert collector.get_last_completed_intent_id() is None

    def test_started_does_not_update_last(self):
        """ToolCallStarted does not update last completed intent."""
        collector = EffectCollector()

        collector.emit(ToolCallStarted(tool_call_id="call_1", tool_name="bash"))

        assert collector.get_last_completed_intent_id() is None

    def test_completed_updates_last(self):
        """ToolCallCompleted updates last completed intent."""
        collector = EffectCollector()

        collector.emit(ToolCallStarted(tool_call_id="call_1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="call_1", tool_name="bash", success=True))

        assert collector.get_last_completed_intent_id() == "call_1"

    def test_rejected_does_not_update_last(self):
        """ToolCallRejected does not update last completed intent."""
        collector = EffectCollector()

        collector.emit(ToolCallStarted(tool_call_id="call_1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="call_1", tool_name="bash", success=True))
        collector.emit(ToolCallStarted(tool_call_id="call_2", tool_name="dangerous"))
        collector.emit(ToolCallRejected(tool_call_id="call_2", tool_name="dangerous", reason="blocked"))

        assert collector.get_last_completed_intent_id() == "call_1"

    def test_multiple_completions_track_last(self):
        """Multiple completions track the most recent."""
        collector = EffectCollector()

        collector.emit(ToolCallStarted(tool_call_id="call_1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="call_1", tool_name="bash", success=True))
        assert collector.get_last_completed_intent_id() == "call_1"

        collector.emit(ToolCallStarted(tool_call_id="call_2", tool_name="write_file"))
        collector.emit(ToolCallCompleted(tool_call_id="call_2", tool_name="write_file", success=True))
        assert collector.get_last_completed_intent_id() == "call_2"

        collector.emit(ToolCallStarted(tool_call_id="call_3", tool_name="read"))
        collector.emit(ToolCallCompleted(tool_call_id="call_3", tool_name="read", success=True))
        assert collector.get_last_completed_intent_id() == "call_3"


# =============================================================================
# Effect Classification Tests
# =============================================================================


class TestEffectClassification:
    """Tests for intent vs lifecycle effect classification."""

    def test_intent_effects_include_tool_calls(self):
        """Intent effects include all tool call types."""
        collector = EffectCollector()

        collector.emit(ToolCallStarted(tool_call_id="c1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="c1", tool_name="bash", success=True))
        collector.emit(ToolCallStarted(tool_call_id="c2", tool_name="write"))
        collector.emit(ToolCallRejected(tool_call_id="c2", tool_name="write", reason="no"))

        intent_effects = collector.get_intent_effects()

        assert len(intent_effects) == 4
        intent_types = {e.effect_type for e in intent_effects}
        assert intent_types == {"tool_call_started", "tool_call_completed", "tool_call_rejected"}

    def test_lifecycle_effects_exclude_tool_calls(self):
        """Lifecycle effects exclude tool call types."""
        collector = EffectCollector()

        collector.emit(TaskStarted(task_name="task"))
        collector.emit(PromptSent(user_prompt="Hello"))
        collector.emit(AgentMessage(content="Output"))
        collector.emit(TaskCompleted(task_name="task"))

        lifecycle_effects = collector.get_lifecycle_effects()

        assert len(lifecycle_effects) == 4
        lifecycle_types = {e.effect_type for e in lifecycle_effects}
        assert lifecycle_types == {"task_started", "prompt_sent", "agent_message", "task_completed"}

    def test_classification_is_disjoint(self):
        """Intent and lifecycle effects are disjoint and complete."""
        collector = EffectCollector()

        collector.emit(TaskStarted(task_name="task"))
        collector.emit(PromptSent(user_prompt="Hello"))
        collector.emit(ToolCallStarted(tool_call_id="c1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="c1", tool_name="bash", success=True))
        collector.emit(AgentMessage(content="Output"))
        collector.emit(ToolCallStarted(tool_call_id="c2", tool_name="write"))
        collector.emit(ToolCallRejected(tool_call_id="c2", tool_name="write", reason="no"))
        collector.emit(TaskCompleted(task_name="task"))

        intent_effects = collector.get_intent_effects()
        lifecycle_effects = collector.get_lifecycle_effects()
        all_effects = collector.get_all_effects()

        # Union should equal all
        assert len(intent_effects) + len(lifecycle_effects) == len(all_effects)

        # No overlap - check by effect_type since effects aren't hashable
        intent_types = {"tool_call_started", "tool_call_completed", "tool_call_rejected"}
        for effect in intent_effects:
            assert effect.effect_type in intent_types
        for effect in lifecycle_effects:
            assert effect.effect_type not in intent_types


# =============================================================================
# Serialization Tests
# =============================================================================


class TestSerialization:
    """Tests for effect serialization and deserialization."""

    def test_serialize_produces_dict(self):
        """serialize_for_transport produces a dictionary."""
        collector = EffectCollector(_id="test")
        collector.emit(TaskStarted(task_name="test"))

        data = collector.serialize_for_transport()

        assert isinstance(data, dict)
        assert "collector_id" in data
        assert "last_completed_intent_id" in data
        assert "effects" in data

    def test_serialize_is_json_compatible(self):
        """Serialized data is JSON-compatible."""
        collector = EffectCollector(_id="test-serialize")

        collector.emit(TaskStarted(task_name="serialize_test"))
        collector.emit(ToolCallStarted(tool_call_id="ser_1", tool_name="bash", params={"cmd": "ls"}))
        collector.emit(ToolCallCompleted(tool_call_id="ser_1", tool_name="bash", success=True))
        collector.emit(AgentMessage(content="Listed files"))
        collector.emit(TaskCompleted(task_name="serialize_test", outputs={"result": "ok"}))

        data = collector.serialize_for_transport()
        json_str = json.dumps(data)

        assert len(json_str) > 0

    def test_deserialize_roundtrip(self):
        """Serialization roundtrip preserves all data."""
        original = EffectCollector(_id="test-roundtrip")

        original.emit(TaskStarted(task_name="roundtrip_test"))
        original.emit(ToolCallStarted(tool_call_id="rt_1", tool_name="bash"))
        original.emit(ToolCallCompleted(tool_call_id="rt_1", tool_name="bash", success=True))
        original.emit(AgentMessage(content="Done"))
        original.emit(TaskCompleted(task_name="roundtrip_test"))

        # Roundtrip through JSON
        data = original.serialize_for_transport()
        json_str = json.dumps(data)
        restored_data = json.loads(json_str)
        restored = EffectCollector.deserialize_from_transport(restored_data)

        assert restored.id == original.id
        assert restored.get_last_completed_intent_id() == original.get_last_completed_intent_id()
        assert len(restored.get_all_effects()) == len(original.get_all_effects())

    def test_deserialize_preserves_effect_types(self):
        """Deserialized effects have correct types."""
        original = EffectCollector(_id="test-types")

        original.emit(TaskStarted(task_name="type_test"))
        original.emit(ToolCallStarted(tool_call_id="t1", tool_name="bash"))
        original.emit(ToolCallCompleted(tool_call_id="t1", tool_name="bash", success=True))

        data = original.serialize_for_transport()
        restored = EffectCollector.deserialize_from_transport(data)

        for orig_eff, rest_eff in zip(original.get_all_effects(), restored.get_all_effects(), strict=True):
            assert orig_eff.effect_type == rest_eff.effect_type
            assert orig_eff.task_name == rest_eff.task_name

    def test_deserialize_empty_collector(self):
        """Empty collector serializes and deserializes correctly."""
        original = EffectCollector(_id="empty")

        data = original.serialize_for_transport()
        restored = EffectCollector.deserialize_from_transport(data)

        assert restored.id == "empty"
        assert len(restored.get_all_effects()) == 0
        assert restored.get_last_completed_intent_id() is None

    def test_deserialize_uses_explicit_registry(self):
        """Transport decode should honor an explicit registry."""

        class TransportOnlyEffect(Effect):
            effect_type: Literal["transport_only_effect"] = "transport_only_effect"
            payload: str = ""

        registry = KERNEL_EFFECT_REGISTRY.extend({"transport_only_effect": TransportOnlyEffect})
        restored = EffectCollector.deserialize_from_transport(
            {
                "collector_id": "test",
                "effects": [{"effect_type": "transport_only_effect", "payload": "x"}],
            },
            registry=registry,
        )

        assert isinstance(restored.get_all_effects()[0], TransportOnlyEffect)
        assert restored.get_all_effects()[0].payload == "x"

    def test_deserialize_decodes_contributorized_effects_with_runtime_registry(self):
        restored = EffectCollector.deserialize_from_transport(
            {
                "collector_id": "test",
                "effects": [
                    {
                        "effect_type": "transfer_initiated",
                        "from_account": "a",
                        "to_account": "b",
                        "amount": 10.0,
                        "currency": "USD",
                        "reference": "demo",
                    }
                ],
            },
            registry=compose_effect_registry(),
        )

        assert isinstance(restored.get_all_effects()[0], TransferInitiated)
        assert restored.get_all_effects()[0].reference == "demo"


# =============================================================================
# Causality Linking Tests
# =============================================================================


class TestCausalityLinking:
    """Tests for causality linking integration."""

    def test_causality_flow_read_then_edit(self):
        """Causality linking works for read-then-edit scenario."""
        collector = EffectCollector(_id="causality-test")

        # Read file
        collector.emit(ToolCallStarted(tool_call_id="read_1", tool_name="Read"))
        collector.emit(ToolCallCompleted(tool_call_id="read_1", tool_name="Read", success=True))

        assert collector.get_last_completed_intent_id() == "read_1"

        # Edit file - result effects would be attributed to this
        collector.emit(ToolCallStarted(tool_call_id="edit_1", tool_name="Edit"))
        collector.emit(ToolCallCompleted(tool_call_id="edit_1", tool_name="Edit", success=True))

        caused_by = collector.get_last_completed_intent_id()
        assert caused_by == "edit_1"

        # Simulate result effect extraction
        result_effect = WorkspacePatchCaptured(
            files_changed=("main.py",),
            patch_hash="abc123",
            caused_by=caused_by,
        )

        assert result_effect.caused_by == "edit_1"

    def test_causality_flow_bash_creates_files(self):
        """Causality linking works for bash creating multiple files."""
        collector = EffectCollector(_id="bash-test")

        collector.emit(ToolCallStarted(tool_call_id="bash_1", tool_name="Bash"))
        collector.emit(ToolCallCompleted(tool_call_id="bash_1", tool_name="Bash", success=True))

        caused_by = collector.get_last_completed_intent_id()

        # All file changes from build link to bash_1
        file1 = FileCreate(path="build/out.o", caused_by=caused_by)
        file2 = FileCreate(path="build/main", caused_by=caused_by)

        assert file1.caused_by == "bash_1"
        assert file2.caused_by == "bash_1"

    def test_no_tool_completion_scenario(self):
        """No-tool-completion scenario has None causality."""
        collector = EffectCollector(_id="no-complete")

        collector.emit(TaskStarted(task_name="simple"))
        collector.emit(PromptSent(user_prompt="Just say hello"))
        collector.emit(AgentMessage(content="Hello!"))
        collector.emit(TaskCompleted(task_name="simple"))

        assert collector.get_last_completed_intent_id() is None

        # File changes (if any) would have caused_by=None
        hypothetical_effect = FileCreate(
            path="surprise.txt",
            caused_by=collector.get_last_completed_intent_id(),
        )
        assert hypothetical_effect.caused_by is None


# =============================================================================
# Repr Tests
# =============================================================================


class TestRepr:
    """Tests for string representation."""

    def test_repr_empty(self):
        """Empty collector has informative repr."""
        collector = EffectCollector(_id="test")
        repr_str = repr(collector)

        assert "test" in repr_str
        assert "effects=0" in repr_str

    def test_repr_with_effects(self):
        """Collector with effects shows counts."""
        collector = EffectCollector(_id="test")
        collector.emit(TaskStarted(task_name="t"))
        collector.emit(ToolCallStarted(tool_call_id="c1", tool_name="bash"))
        collector.emit(ToolCallCompleted(tool_call_id="c1", tool_name="bash", success=True))

        repr_str = repr(collector)

        assert "effects=3" in repr_str
        assert "intent=2" in repr_str
        assert "lifecycle=1" in repr_str


# =============================================================================
# Non-Pydantic Effect Serialization Tests
# =============================================================================


class TestNonPydanticEffectSerialization:
    """Tests for serialization of non-Pydantic effects (fallback paths)."""

    def test_pydantic_effect_uses_model_dump(self):
        """Pydantic-based effects serialize via model_dump()."""
        collector = EffectCollector(_id="test-pydantic")

        # All standard effects are Pydantic models
        effect = TaskStarted(task_name="pydantic_test")
        collector.emit(effect)

        data = collector.serialize_for_transport()

        # Verify serialization succeeded
        assert len(data["effects"]) == 1
        serialized = data["effects"][0]
        assert serialized["effect_type"] == "task_started"
        assert serialized["task_name"] == "pydantic_test"

    def test_dataclass_effect_uses_asdict_fallback(self, caplog):
        """Non-Pydantic dataclass effects use dataclasses.asdict() fallback."""
        import dataclasses
        import logging

        @dataclasses.dataclass
        class DataclassEffect:
            """A non-Pydantic effect implemented as a dataclass."""

            effect_type: str = "dataclass_test"
            task_name: str | None = None
            custom_field: str = "custom_value"

        collector = EffectCollector(_id="test-dataclass")
        effect = DataclassEffect(task_name="dc_test", custom_field="hello")
        collector._collected_effects.append(effect)  # Bypass emit to test serialization

        with caplog.at_level(logging.WARNING):
            data = collector.serialize_for_transport()

        # Verify warning was logged
        assert "Non-Pydantic effect encountered" in caplog.text
        assert "DataclassEffect" in caplog.text
        assert "fallback serialization" in caplog.text

        # Verify serialization succeeded
        assert len(data["effects"]) == 1
        serialized = data["effects"][0]
        assert serialized["effect_type"] == "dataclass_test"
        assert serialized["task_name"] == "dc_test"
        assert serialized["custom_field"] == "hello"

    def test_regular_object_uses_vars_fallback(self, caplog):
        """Non-Pydantic regular objects use vars() fallback."""
        import logging

        class RegularEffect:
            """A plain Python class effect (not dataclass, not Pydantic)."""

            def __init__(self):
                self.effect_type = "regular_test"
                self.task_name = "regular_task"
                self.value = 42

        collector = EffectCollector(_id="test-regular")
        effect = RegularEffect()
        collector._collected_effects.append(effect)  # Bypass emit to test serialization

        with caplog.at_level(logging.WARNING):
            data = collector.serialize_for_transport()

        # Verify warning was logged
        assert "Non-Pydantic effect encountered" in caplog.text
        assert "RegularEffect" in caplog.text

        # Verify serialization succeeded
        assert len(data["effects"]) == 1
        serialized = data["effects"][0]
        assert serialized["effect_type"] == "regular_test"
        assert serialized["task_name"] == "regular_task"
        assert serialized["value"] == 42

    def test_mixed_pydantic_and_non_pydantic_effects(self, caplog):
        """Collector correctly serializes mixed Pydantic and non-Pydantic effects."""
        import dataclasses
        import logging

        @dataclasses.dataclass
        class CustomEffect:
            effect_type: str = "custom"
            task_name: str | None = None
            data: str = ""

        collector = EffectCollector(_id="test-mixed")

        # Add a mix of Pydantic and non-Pydantic effects
        collector.emit(TaskStarted(task_name="start"))
        collector._collected_effects.append(CustomEffect(task_name="custom", data="test"))
        collector.emit(TaskCompleted(task_name="start"))

        with caplog.at_level(logging.WARNING):
            data = collector.serialize_for_transport()

        # Verify warning was logged for the non-Pydantic effect only
        assert caplog.text.count("Non-Pydantic effect encountered") == 1
        assert "CustomEffect" in caplog.text

        # Verify all effects were serialized
        assert len(data["effects"]) == 3
        assert data["effects"][0]["effect_type"] == "task_started"
        assert data["effects"][1]["effect_type"] == "custom"
        assert data["effects"][1]["data"] == "test"
        assert data["effects"][2]["effect_type"] == "task_completed"

    def test_non_pydantic_effect_serialization_is_json_compatible(self, caplog):
        """Non-Pydantic effects serialize to JSON-compatible dicts."""
        import dataclasses
        import logging

        @dataclasses.dataclass
        class JsonTestEffect:
            effect_type: str = "json_test"
            task_name: str | None = None
            numbers: tuple[int, ...] = ()

        collector = EffectCollector(_id="test-json")
        # Use a tuple to verify it's converted correctly
        effect = JsonTestEffect(task_name="json", numbers=(1, 2, 3))
        collector._collected_effects.append(effect)

        with caplog.at_level(logging.WARNING):
            data = collector.serialize_for_transport()

        # Verify JSON serialization works
        json_str = json.dumps(data)
        assert len(json_str) > 0

        # Roundtrip verification
        restored = json.loads(json_str)
        assert restored["effects"][0]["effect_type"] == "json_test"
        assert restored["effects"][0]["numbers"] == [1, 2, 3]  # tuple becomes list in JSON
