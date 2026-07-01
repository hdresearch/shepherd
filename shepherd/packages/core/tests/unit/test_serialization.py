"""Tests for serialization round-trips of core types and effects.

These tests verify that:
1. Pydantic models (ToolCall, ToolResult, ExecutionResult) serialize and deserialize correctly
2. Effects containing nested objects (DiffPatch, etc.) serialize correctly
3. The caching infrastructure can rely on model_dump() / model_validate()
"""

from shepherd_core.effects import (
    DiffPatch,
    TaskCompleted,
    TaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
    effect_from_dict,
)
from shepherd_core.types import (
    ExecutionResult,
    ProviderCapabilities,
    ToolCall,
    ToolContext,
    ToolResult,
    TraceConfig,
    ValidationResult,
)

# =============================================================================
# ToolCall Serialization
# =============================================================================


class TestToolCallSerialization:
    """Tests for ToolCall serialization."""

    def test_round_trip_basic(self):
        """Basic ToolCall should round-trip correctly."""
        original = ToolCall(id="tc_123", name="Read", params={"path": "/foo/bar.py"})

        data = original.model_dump()
        restored = ToolCall.model_validate(data)

        assert restored == original
        assert restored.id == "tc_123"
        assert restored.name == "Read"
        assert restored.params == {"path": "/foo/bar.py"}

    def test_round_trip_empty_params(self):
        """ToolCall with empty params should round-trip correctly."""
        original = ToolCall(id="tc_456", name="ListFiles")

        data = original.model_dump()
        restored = ToolCall.model_validate(data)

        assert restored == original
        assert restored.params == {}

    def test_round_trip_complex_params(self):
        """ToolCall with nested params should round-trip correctly."""
        original = ToolCall(
            id="tc_789",
            name="Edit",
            params={
                "file_path": "/src/main.py",
                "old_string": "def foo():",
                "new_string": "def bar():",
                "metadata": {"line": 42, "context": ["import os", ""]},
            },
        )

        data = original.model_dump()
        restored = ToolCall.model_validate(data)

        assert restored == original
        assert restored.params["metadata"]["line"] == 42


# =============================================================================
# ToolResult Serialization
# =============================================================================


class TestToolResultSerialization:
    """Tests for ToolResult serialization."""

    def test_round_trip_success(self):
        """Successful ToolResult should round-trip correctly."""
        original = ToolResult(
            tool_call_id="tc_123",
            success=True,
            output="File content here",
        )

        data = original.model_dump()
        restored = ToolResult.model_validate(data)

        assert restored == original
        assert restored.success is True
        assert restored.output == "File content here"
        assert restored.error is None

    def test_round_trip_failure(self):
        """Failed ToolResult should round-trip correctly."""
        original = ToolResult(
            tool_call_id="tc_456",
            success=False,
            error="File not found: /missing.txt",
        )

        data = original.model_dump()
        restored = ToolResult.model_validate(data)

        assert restored == original
        assert restored.success is False
        assert restored.error == "File not found: /missing.txt"

    def test_round_trip_complex_output(self):
        """ToolResult with complex output should round-trip correctly."""
        original = ToolResult(
            tool_call_id="tc_789",
            success=True,
            output={"files": ["a.py", "b.py"], "count": 2},
        )

        data = original.model_dump()
        restored = ToolResult.model_validate(data)

        assert restored == original
        assert restored.output["count"] == 2


# =============================================================================
# ExecutionResult Serialization
# =============================================================================


class TestExecutionResultSerialization:
    """Tests for ExecutionResult serialization (critical for caching)."""

    def test_round_trip_minimal(self):
        """Minimal ExecutionResult should round-trip correctly."""
        original = ExecutionResult(success=True, output_text="Hello, world!")

        data = original.model_dump()
        restored = ExecutionResult.model_validate(data)

        assert restored == original
        assert restored.success is True
        assert restored.output_text == "Hello, world!"

    def test_round_trip_with_tool_calls(self):
        """ExecutionResult with tool calls should round-trip correctly.

        This is the critical test for caching - we need to verify that
        nested ToolCall and ToolResult objects serialize and deserialize
        as proper Pydantic models, not just dicts.
        """
        tool_call = ToolCall(id="tc_1", name="Read", params={"file_path": "/foo.py"})
        tool_result = ToolResult(tool_call_id="tc_1", success=True, output="content")

        original = ExecutionResult(
            success=True,
            output_text="Read the file",
            tool_calls=(tool_call,),
            tool_results=(tool_result,),
        )

        data = original.model_dump()
        restored = ExecutionResult.model_validate(data)

        assert restored == original

        # Verify nested types are proper Pydantic models, not dicts
        assert isinstance(restored.tool_calls[0], ToolCall)
        assert isinstance(restored.tool_results[0], ToolResult)
        assert restored.tool_calls[0].name == "Read"
        assert restored.tool_results[0].success is True

    def test_round_trip_full(self):
        """Full ExecutionResult with all fields should round-trip correctly."""
        original = ExecutionResult(
            success=True,
            output_text="Completed analysis",
            structured_output={"findings": ["issue1", "issue2"], "severity": "medium"},
            tool_calls=(
                ToolCall(id="tc_1", name="Read", params={"file_path": "/a.py"}),
                ToolCall(id="tc_2", name="Grep", params={"pattern": "TODO"}),
                ToolCall(id="tc_3", name="Write", params={"file_path": "/b.py", "content": "new"}),
            ),
            tool_results=(
                ToolResult(tool_call_id="tc_1", success=True, output="content of a.py"),
                ToolResult(tool_call_id="tc_2", success=True, output="line 42: TODO fix"),
                ToolResult(tool_call_id="tc_3", success=True, output="Written"),
            ),
            session_id="session_abc123",
            metadata={"model": "claude-sonnet", "tokens": 1500},
        )

        data = original.model_dump()
        restored = ExecutionResult.model_validate(data)

        assert restored == original
        assert len(restored.tool_calls) == 3
        assert len(restored.tool_results) == 3
        assert restored.session_id == "session_abc123"
        assert restored.metadata["tokens"] == 1500

        # Verify all nested types
        for tc in restored.tool_calls:
            assert isinstance(tc, ToolCall)
        for tr in restored.tool_results:
            assert isinstance(tr, ToolResult)

    def test_round_trip_failure_result(self):
        """Failed ExecutionResult should round-trip correctly."""
        original = ExecutionResult(
            success=False,
            output_text="",
            metadata={"error": "Rate limited", "retry_after": 60},
        )

        data = original.model_dump()
        restored = ExecutionResult.model_validate(data)

        assert restored == original
        assert restored.success is False

    def test_tuples_restored_from_json_lists(self):
        """Verify that tuple fields are properly restored from JSON lists.

        Pydantic's model_dump() preserves tuples, but JSON serialization
        converts them to lists. model_validate() should handle both.
        """
        import json

        original = ExecutionResult(
            success=True,
            tool_calls=(ToolCall(id="1", name="A"),),
            tool_results=(ToolResult(tool_call_id="1", success=True),),
        )

        # model_dump() preserves tuples
        data = original.model_dump()
        assert isinstance(data["tool_calls"], tuple)

        # JSON round-trip converts to lists
        json_str = json.dumps(data, default=str)
        json_data = json.loads(json_str)
        assert isinstance(json_data["tool_calls"], list)

        # model_validate() should handle both tuples and lists
        from_tuple = ExecutionResult.model_validate(data)
        from_list = ExecutionResult.model_validate(json_data)

        assert isinstance(from_tuple.tool_calls, tuple)
        assert isinstance(from_list.tool_calls, tuple)
        assert from_tuple == from_list


# =============================================================================
# Other Pydantic Types Serialization
# =============================================================================


class TestOtherTypesSerialization:
    """Tests for other Pydantic model serialization."""

    def test_validation_result_round_trip(self):
        """ValidationResult should round-trip correctly."""
        tool = ToolCall(id="tc_1", name="Bash", params={"command": "rm -rf /"})
        original = ValidationResult.reject(tool, "Dangerous command blocked")

        data = original.model_dump()
        restored = ValidationResult.model_validate(data)

        assert restored == original
        assert restored.allowed is False
        assert isinstance(restored.tool, ToolCall)

    def test_tool_context_round_trip(self):
        """ToolContext should round-trip correctly."""
        original = ToolContext(
            context_id="ctx_123",
            tool_name="Read",
            tool_call_id="tc_456",
        )

        data = original.model_dump()
        restored = ToolContext.model_validate(data)

        assert restored == original

    def test_provider_capabilities_round_trip(self):
        """ProviderCapabilities should round-trip correctly."""
        original = ProviderCapabilities(
            provider_type="claude",
            supports_streaming=True,
            supports_tools=True,
            supports_session=True,
            max_tools=50,
            available_tools=frozenset({"Read", "Write", "Bash"}),
        )

        data = original.model_dump()
        restored = ProviderCapabilities.model_validate(data)

        assert restored.provider_type == original.provider_type
        assert restored.supports_session == original.supports_session
        assert restored.max_tools == 50
        # Note: frozenset may deserialize as list/set - check contents
        assert set(restored.available_tools) == {"Read", "Write", "Bash"}

    def test_trace_config_round_trip(self):
        """TraceConfig should round-trip correctly."""
        original = TraceConfig.full()

        data = original.model_dump()
        restored = TraceConfig.model_validate(data)

        assert restored == original
        assert restored.capture_prompts is True


# =============================================================================
# Effect Serialization
# =============================================================================


class TestEffectSerialization:
    """Tests for Effect serialization (dataclass-based with custom model_dump)."""

    def test_simple_effect_round_trip(self):
        """Simple effect should round-trip via effect_from_dict."""
        original = TaskStarted(
            task_name="FixBug",
            provider_id="claude-sonnet",
            inputs={"prompt": "Fix the authentication bug"},
        )

        data = original.model_dump()
        restored = effect_from_dict(data)

        assert isinstance(restored, TaskStarted)
        assert restored.task_name == "FixBug"
        assert restored.provider_id == "claude-sonnet"
        assert restored.inputs["prompt"] == "Fix the authentication bug"

    def test_tool_call_effect_round_trip(self):
        """ToolCallCompleted effect should round-trip correctly."""
        original = ToolCallCompleted(
            task_name="ReadFile",
            provider_id="claude-sonnet",
            tool_call_id="tc_123",
            tool_name="Read",
            success=True,
            output="def main():\n    ...",
        )

        data = original.model_dump()
        restored = effect_from_dict(data)

        assert isinstance(restored, ToolCallCompleted)
        assert restored.tool_call_id == "tc_123"
        assert restored.success is True

    def test_effect_with_dict_params(self):
        """Effect with dict fields should serialize correctly."""
        original = ToolCallStarted(
            task_name="EditFile",
            provider_id="claude-sonnet",
            tool_call_id="tc_456",
            tool_name="Edit",
            params={"file_path": "/foo.py", "old_string": "x", "new_string": "y"},
        )

        data = original.model_dump()
        restored = effect_from_dict(data)

        assert restored.params == original.params
        assert restored.params["file_path"] == "/foo.py"

    def test_effect_type_key_used(self):
        """model_dump should include effect_type for registry lookup."""
        effect = TaskCompleted(
            task_name="Test",
            provider_id="test",
            outputs={},
            duration_ms=100.0,
        )

        data = effect.model_dump()

        # Should include effect_type field for registry lookup
        assert data["effect_type"] == "task_completed"


class TestDiffPatchSha256:
    """Tests for DiffPatch.sha256 auto-computation via @model_validator."""

    def test_sha256_auto_computed_on_construction(self):
        """DiffPatch auto-computes sha256 for non-empty patches."""
        patch = DiffPatch(patch="some diff content", files_changed=("file.txt",))
        assert patch.sha256 is not None
        assert len(patch.sha256) == 64  # Full SHA-256 hex string

    def test_sha256_matches_expected_value(self):
        """Verify sha256 computation is correct."""
        import hashlib

        content = "test patch content"
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()

        patch = DiffPatch(patch=content, files_changed=("test.py",))
        assert patch.sha256 == expected

    def test_empty_patch_has_no_sha256(self):
        """Empty patches have sha256=None."""
        patch = DiffPatch(patch="", files_changed=())
        assert patch.sha256 is None

    def test_whitespace_only_patch_has_no_sha256(self):
        """Whitespace-only patches are treated as empty (sha256=None)."""
        patch = DiffPatch(patch="   \n\t  ", files_changed=())
        assert patch.sha256 is None

    def test_explicit_sha256_not_overwritten(self):
        """If sha256 is explicitly provided, it's not recomputed."""
        explicit_sha = "custom_sha256_value_that_should_not_change"
        patch = DiffPatch(
            patch="some content",
            files_changed=("file.txt",),
            sha256=explicit_sha,
        )
        assert patch.sha256 == explicit_sha

    def test_from_diff_still_works(self):
        """from_diff() factory method still computes sha256 correctly."""
        import hashlib

        content = "diff --git a/file.txt b/file.txt\n+new line"
        expected = hashlib.sha256((content + "\n").encode()).hexdigest()

        patch = DiffPatch.from_diff(content, ("file.txt",), source_step="TestTask")
        assert patch.sha256 == expected
        assert patch.source_step == "TestTask"
        assert patch.files_changed == ("file.txt",)

    def test_serialization_roundtrip_preserves_sha256(self):
        """sha256 is preserved through serialization round-trip."""
        original = DiffPatch(patch="test content", files_changed=("a.py",))
        original_sha = original.sha256

        # Serialize and deserialize
        data = original.model_dump()
        restored = DiffPatch.model_validate(data)

        assert restored.sha256 == original_sha
        assert restored == original

    def test_model_copy_preserves_sha256(self):
        """model_copy() preserves the sha256 field."""
        original = DiffPatch(patch="test content", files_changed=("a.py",))
        copied = original.model_copy()

        assert copied.sha256 == original.sha256
        assert copied == original
