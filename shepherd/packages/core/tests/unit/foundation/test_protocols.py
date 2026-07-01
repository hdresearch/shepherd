"""Tests for kernel protocol compliance."""

from shepherd_core import Effect, EffectLayer, Stream
from shepherd_core.foundation.protocols import (
    EffectProtocol,
)


class TestEffectProtocolCompliance:
    """Tests that Effect class satisfies EffectProtocol."""

    def test_effect_satisfies_protocol(self):
        """Effect class has all required attributes."""
        effect = Effect(effect_type="test")

        # Check required attributes
        assert hasattr(effect, "effect_type")
        assert hasattr(effect, "timestamp")
        assert hasattr(effect, "task_name")
        assert hasattr(effect, "context_id")
        assert hasattr(effect, "binding_name")

    def test_effect_isinstance_check(self):
        """Effect satisfies runtime_checkable EffectProtocol."""
        effect = Effect(effect_type="test")
        assert isinstance(effect, EffectProtocol)

    def test_effect_with_attribution_returns_same_type(self):
        """with_attribution returns a new Effect."""
        effect = Effect(effect_type="test")
        new_effect = effect.with_attribution(task_name="my_task")

        assert new_effect.task_name == "my_task"
        assert new_effect is not effect  # New instance
        assert isinstance(new_effect, EffectProtocol)

    def test_effect_protocol_attributes_have_correct_types(self):
        """Effect attributes have correct types."""
        effect = Effect(effect_type="test", task_name="task1", context_id="ctx1")

        assert isinstance(effect.effect_type, str)
        assert isinstance(effect.timestamp, float)
        assert effect.task_name is None or isinstance(effect.task_name, str)
        assert effect.context_id is None or isinstance(effect.context_id, str)


class TestEffectLayerProtocolCompliance:
    """Tests that EffectLayer class satisfies EffectLayerProtocol."""

    def test_effect_layer_has_required_attributes(self):
        """EffectLayer has all required attributes."""
        effect = Effect(effect_type="test")
        layer = EffectLayer(effect=effect, sequence=0)

        assert hasattr(layer, "effect")
        assert hasattr(layer, "sequence")
        assert hasattr(layer, "source_context")
        assert hasattr(layer, "scope_id")
        assert hasattr(layer, "scope_depth")

    def test_effect_layer_attribute_types(self):
        """EffectLayer attributes have correct types."""
        effect = Effect(effect_type="test")
        layer = EffectLayer(
            effect=effect,
            sequence=5,
            source_context="ctx1",
            scope_id="scope1",
            scope_depth=2,
        )

        assert isinstance(layer.effect, EffectProtocol)
        assert isinstance(layer.sequence, int)
        assert layer.source_context is None or isinstance(layer.source_context, str)
        assert layer.scope_id is None or isinstance(layer.scope_id, str)
        assert isinstance(layer.scope_depth, int)


class TestStreamProtocolCompliance:
    """Tests that Stream class satisfies StreamProtocol."""

    def test_stream_has_layers_property(self):
        """Stream has layers property returning tuple."""
        stream = Stream()
        assert hasattr(stream, "layers")
        assert isinstance(stream.layers, tuple)

    def test_stream_append_returns_new_stream(self):
        """Stream.append returns a new Stream."""
        stream = Stream()
        effect = Effect(effect_type="test")
        new_stream = stream.append(effect)

        assert new_stream is not stream
        assert isinstance(new_stream, Stream)
        assert len(new_stream) == 1
        assert len(stream) == 0  # Original unchanged

    def test_stream_query_yields_layers(self):
        """Stream.query yields layers."""
        stream = Stream()
        stream = stream.append(Effect(effect_type="test1", task_name="task1"))
        stream = stream.append(Effect(effect_type="test2", task_name="task2"))

        layers = list(stream.query(task_name="task1"))
        assert len(layers) == 1
        assert layers[0].effect.effect_type == "test1"

    def test_stream_is_iterable(self):
        """Stream is iterable."""
        stream = Stream()
        stream = stream.append(Effect(effect_type="test"))

        for layer in stream:
            assert hasattr(layer, "effect")

    def test_stream_has_len(self):
        """Stream supports len()."""
        stream = Stream()
        assert len(stream) == 0

        stream = stream.append(Effect(effect_type="test"))
        assert len(stream) == 1


class TestProtocolsAreRuntimeCheckable:
    """Tests that protocols work with isinstance at runtime."""

    def test_effect_protocol_is_runtime_checkable(self):
        """EffectProtocol can be used with isinstance."""
        effect = Effect(effect_type="test")
        assert isinstance(effect, EffectProtocol)

        # Non-conforming object should fail
        class NotAnEffect:
            pass

        assert not isinstance(NotAnEffect(), EffectProtocol)
