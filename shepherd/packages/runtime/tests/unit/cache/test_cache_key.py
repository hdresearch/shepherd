"""Tests for ExecutionKey computation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from shepherd_core.types import ReversibilityLevel
from shepherd_runtime.cache import CachePolicy, ExecutionKey
from shepherd_runtime.context import BindableContext
from shepherd_runtime.scope import Scope
from shepherd_runtime.task.metadata import FieldInfo, TaskMetadata
from shepherd_tests import MockProvider

# --- Test Context ---


@dataclass(frozen=True)
class SampleContext(BindableContext):
    """Test context with state_hash support."""

    __binding_name__: ClassVar[str] = "test"
    value: str = ""
    _frozen_id: str | None = None

    @property
    def context_id(self) -> str:
        return self._frozen_id or f"test:{self.value}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        return ReversibilityLevel.AUTO

    def state_hash(self, scope) -> str:
        """Return hash based on value."""
        import hashlib

        return hashlib.sha256(self.value.encode()).hexdigest()[:12]


# --- Test Metadata ---


def make_test_metadata(name: str = "TestTask", docstring: str = "Test task") -> TaskMetadata:
    """Create a simple test metadata."""
    return TaskMetadata(
        name=name,
        docstring=docstring,
        inputs={"input1": FieldInfo(name="input1", inner_type=str, marker_type="input")},
        outputs={"output1": FieldInfo(name="output1", inner_type=str, marker_type="output")},
    )


# --- Tests ---


class TestExecutionKeyDeterminism:
    """Test that execution keys are deterministic."""

    def test_same_inputs_produce_same_key(self):
        """Same inputs should always produce the same key."""
        meta = make_test_metadata()
        inputs = {"input1": "hello world"}

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key1 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.STRICT)

            assert key1.key == key2.key
            assert key1.task_name == key2.task_name
            assert key1.inputs_hash == key2.inputs_hash

    def test_different_inputs_produce_different_keys(self):
        """Different inputs should produce different keys."""
        meta = make_test_metadata()

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key1 = ExecutionKey.compute(meta, {"input1": "hello"}, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta, {"input1": "world"}, scope, CachePolicy.STRICT)

            assert key1.key != key2.key
            assert key1.inputs_hash != key2.inputs_hash

    def test_key_is_16_chars(self):
        """Combined key should be 16 hex characters."""
        meta = make_test_metadata()

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key = ExecutionKey.compute(meta, {"input1": "test"}, scope, CachePolicy.STRICT)

            assert len(key.key) == 16
            assert all(c in "0123456789abcdef" for c in key.key)


class TestExecutionKeyComponents:
    """Test individual key components."""

    def test_task_key_includes_name(self):
        """Task key should be based on task name."""
        meta1 = make_test_metadata(name="Task1")
        meta2 = make_test_metadata(name="Task2")
        inputs = {"input1": "test"}

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key1 = ExecutionKey.compute(meta1, inputs, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta2, inputs, scope, CachePolicy.STRICT)

            assert key1.task_name != key2.task_name
            assert key1.task_key != key2.task_key
            assert key1.key != key2.key

    def test_inputs_hash_is_stable(self):
        """Inputs hash should be stable across calls."""
        meta = make_test_metadata()
        inputs = {"input1": "test value"}

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key1 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta, dict(inputs), scope, CachePolicy.STRICT)

            assert key1.inputs_hash == key2.inputs_hash

    def test_contexts_hash_changes_with_context(self):
        """Contexts hash should change when context state changes."""
        meta = make_test_metadata()
        inputs = {"input1": "test"}

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)
            ctx1 = SampleContext(value="state1")
            scope.bind("test", ctx1)

            key1 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.STRICT)

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)
            ctx2 = SampleContext(value="state2")
            scope.bind("test", ctx2)

            key2 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.STRICT)

        assert key1.contexts_hash != key2.contexts_hash
        assert key1.key != key2.key


class TestCachePolicyEffects:
    """Test how cache policy affects key computation."""

    def test_strict_includes_docstring(self):
        """STRICT policy should include docstring in key."""
        meta1 = make_test_metadata(docstring="Doc version 1")
        meta2 = make_test_metadata(docstring="Doc version 2")
        inputs = {"input1": "test"}

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key1 = ExecutionKey.compute(meta1, inputs, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta2, inputs, scope, CachePolicy.STRICT)

            # Keys should differ because docstrings differ
            assert key1.task_key != key2.task_key
            assert key1.key != key2.key

    def test_relaxed_ignores_docstring(self):
        """RELAXED policy should ignore docstring changes."""
        meta1 = make_test_metadata(docstring="Doc version 1")
        meta2 = make_test_metadata(docstring="Doc version 2")
        inputs = {"input1": "test"}

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key1 = ExecutionKey.compute(meta1, inputs, scope, CachePolicy.RELAXED)
            key2 = ExecutionKey.compute(meta2, inputs, scope, CachePolicy.RELAXED)

            # Task keys should be same because docstrings are ignored
            assert key1.task_key == key2.task_key

    def test_inputs_only_ignores_context_state(self):
        """INPUTS_ONLY policy should ignore context state."""
        meta = make_test_metadata()
        inputs = {"input1": "test"}

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)
            ctx1 = SampleContext(value="state1")
            scope.bind("test", ctx1)

            key1 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.INPUTS_ONLY)

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)
            ctx2 = SampleContext(value="state2")
            scope.bind("test", ctx2)

            key2 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.INPUTS_ONLY)

        # Keys should be same because context is ignored
        assert key1.contexts_hash == key2.contexts_hash
        assert key1.contexts_hash == "0" * 16


class TestInputSerialization:
    """Test input value serialization for hashing."""

    def test_list_inputs(self):
        """List inputs should be serialized correctly."""
        meta = make_test_metadata()

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key1 = ExecutionKey.compute(meta, {"input1": ["a", "b", "c"]}, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta, {"input1": ["a", "b", "c"]}, scope, CachePolicy.STRICT)
            key3 = ExecutionKey.compute(meta, {"input1": ["a", "b", "d"]}, scope, CachePolicy.STRICT)

            assert key1.inputs_hash == key2.inputs_hash
            assert key1.inputs_hash != key3.inputs_hash

    def test_dict_inputs(self):
        """Dict inputs should be serialized correctly (order-independent)."""
        meta = make_test_metadata()

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            # Different key order should produce same hash
            key1 = ExecutionKey.compute(meta, {"input1": {"a": 1, "b": 2}}, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta, {"input1": {"b": 2, "a": 1}}, scope, CachePolicy.STRICT)

            assert key1.inputs_hash == key2.inputs_hash

    def test_nested_inputs(self):
        """Nested structures should be serialized correctly."""
        meta = make_test_metadata()

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            inputs = {"input1": {"nested": [1, 2, {"deep": "value"}]}}
            key1 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.STRICT)
            key2 = ExecutionKey.compute(meta, inputs, scope, CachePolicy.STRICT)

            assert key1.inputs_hash == key2.inputs_hash


class TestExecutionKeyStr:
    """Test string representation."""

    def test_str_format(self):
        """String representation should include task name and key."""
        meta = make_test_metadata(name="MyTask")

        with Scope(root=True) as scope:
            scope.register_provider("default", MockProvider(), default=True)

            key = ExecutionKey.compute(meta, {"input1": "test"}, scope, CachePolicy.STRICT)

            str_repr = str(key)
            assert "MyTask" in str_repr
            assert key.key in str_repr
