"""Tests for enriched effects with computed preview properties.

These tests verify the Phase 1 effect enrichment changes:
- Full content stored in effects (output, system_prompt, user_prompt, outputs)
- Preview properties computed from full content (not stored)
- Serialization excludes computed properties
- Round-trip serialization preserves full content
"""

from shepherd_core.effects import (
    PREVIEW_LENGTH_PROMPT,
    PREVIEW_LENGTH_STEP_SUMMARY,
    PREVIEW_LENGTH_TOOL_OUTPUT,
    PromptSent,
    StepCompleted,
    ToolCallCompleted,
    effect_from_dict,
)


class TestToolCallCompleted:
    """Tests for ToolCallCompleted with full output."""

    def test_output_stored_in_full(self):
        effect = ToolCallCompleted(
            tool_call_id="tc_001",
            tool_name="Read",
            output="Hello, world!",
        )
        assert effect.output == "Hello, world!"

    def test_output_preview_computed(self):
        effect = ToolCallCompleted(
            tool_call_id="tc_002",
            tool_name="Read",
            output="Hello, world!",
        )
        assert effect.output_preview == "Hello, world!"

    def test_output_preview_truncates_long_output(self):
        long_output = "x" * 1000
        effect = ToolCallCompleted(
            tool_call_id="tc_003",
            tool_name="Bash",
            output=long_output,
        )
        assert len(effect.output_preview) == PREVIEW_LENGTH_TOOL_OUTPUT + 3  # + "..."
        assert effect.output_preview.endswith("...")
        assert effect.output == long_output

    def test_serialization_excludes_preview(self):
        effect = ToolCallCompleted(
            tool_call_id="tc_004",
            tool_name="Read",
            output="content",
        )
        data = effect.model_dump()
        assert "output" in data
        assert "output_preview" not in data

    def test_round_trip_serialization(self):
        original = ToolCallCompleted(
            tool_call_id="tc_005",
            tool_name="Bash",
            output="full output content",
            success=True,
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert restored.output == original.output
        assert restored.output_preview == original.output_preview


class TestPromptSent:
    """Tests for PromptSent with full prompts."""

    def test_full_prompts_stored(self):
        effect = PromptSent(
            system_prompt="You are a helpful assistant.",
            user_prompt="What is 2+2?",
        )
        assert effect.system_prompt == "You are a helpful assistant."
        assert effect.user_prompt == "What is 2+2?"

    def test_preview_properties_computed(self):
        long_prompt = "x" * 500
        effect = PromptSent(
            system_prompt=long_prompt,
            user_prompt=long_prompt,
        )
        assert len(effect.system_prompt_preview) == PREVIEW_LENGTH_PROMPT + 3  # + "..."
        assert len(effect.user_prompt_preview) == PREVIEW_LENGTH_PROMPT + 3

    def test_serialization_excludes_previews(self):
        effect = PromptSent(
            system_prompt="system",
            user_prompt="user",
        )
        data = effect.model_dump()
        assert "system_prompt" in data
        assert "user_prompt" in data
        assert "system_prompt_preview" not in data
        assert "user_prompt_preview" not in data

    def test_round_trip_serialization(self):
        original = PromptSent(
            system_prompt="You are a coding assistant.",
            user_prompt="Write a function to sort a list.",
            total_tokens=50,
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert restored.system_prompt == original.system_prompt
        assert restored.user_prompt == original.user_prompt
        assert restored.system_prompt_preview == original.system_prompt_preview
        assert restored.user_prompt_preview == original.user_prompt_preview


class TestStepCompleted:
    """Tests for StepCompleted with full outputs (Any type)."""

    def test_outputs_dict_stored(self):
        """Dict outputs are stored correctly."""
        effect = StepCompleted(
            step_name="analyze",
            parent_task="Pipeline",
            outputs={"result": "success", "count": 42},
        )
        assert effect.outputs == {"result": "success", "count": 42}

    def test_outputs_list_stored(self):
        """List outputs are stored correctly (Any type allows this)."""
        effect = StepCompleted(
            step_name="collect",
            parent_task="Pipeline",
            outputs=["item1", "item2", "item3"],
        )
        assert effect.outputs == ["item1", "item2", "item3"]

    def test_outputs_string_stored(self):
        """String outputs are stored correctly (Any type allows this)."""
        effect = StepCompleted(
            step_name="summarize",
            parent_task="Pipeline",
            outputs="The analysis is complete.",
        )
        assert effect.outputs == "The analysis is complete."

    def test_outputs_none_allowed(self):
        effect = StepCompleted(
            step_name="void_step",
            parent_task="Pipeline",
            outputs=None,
        )
        assert effect.outputs is None
        assert effect.outputs_summary == ""

    def test_outputs_summary_computed(self):
        effect = StepCompleted(
            step_name="analyze",
            parent_task="Pipeline",
            outputs={"result": "success"},
        )
        assert "result" in effect.outputs_summary

    def test_outputs_summary_truncates_long_output(self):
        effect = StepCompleted(
            step_name="analyze",
            parent_task="Pipeline",
            outputs={"data": "x" * 200},
        )
        assert len(effect.outputs_summary) == PREVIEW_LENGTH_STEP_SUMMARY + 3  # + "..."
        assert effect.outputs_summary.endswith("...")

    def test_serialization_excludes_summary(self):
        effect = StepCompleted(
            step_name="analyze",
            parent_task="Pipeline",
            outputs={"data": "value"},
        )
        data = effect.model_dump()
        assert "outputs" in data
        assert "outputs_summary" not in data

    def test_round_trip_serialization_dict(self):
        original = StepCompleted(
            step_name="process",
            parent_task="Task",
            outputs={"key": "value", "nested": {"a": 1}},
            duration_ms=123.45,
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert restored.outputs == original.outputs
        assert restored.outputs_summary == original.outputs_summary

    def test_round_trip_serialization_list(self):
        original = StepCompleted(
            step_name="collect",
            parent_task="Task",
            outputs=[1, 2, 3, "four"],
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert restored.outputs == original.outputs


class TestEdgeCases:
    """Edge case tests for enriched effects."""

    def test_empty_output(self):
        """Empty string output produces empty preview, not '...' or None."""
        effect = ToolCallCompleted(tool_call_id="tc", tool_name="Read", output="")
        assert effect.output == ""
        assert effect.output_preview == ""

    def test_whitespace_preserved(self):
        """Whitespace-only content is preserved."""
        effect = ToolCallCompleted(tool_call_id="tc", tool_name="Read", output="   ")
        assert effect.output == "   "
        assert effect.output_preview == "   "

    def test_boundary_at_limit_no_truncation(self):
        """Exactly at limit should not be truncated."""
        effect = ToolCallCompleted(
            tool_call_id="tc",
            tool_name="Read",
            output="x" * PREVIEW_LENGTH_TOOL_OUTPUT,
        )
        assert effect.output_preview == "x" * PREVIEW_LENGTH_TOOL_OUTPUT
        assert not effect.output_preview.endswith("...")

    def test_boundary_over_limit_truncated(self):
        """One char over limit should be truncated."""
        effect = ToolCallCompleted(
            tool_call_id="tc",
            tool_name="Read",
            output="x" * (PREVIEW_LENGTH_TOOL_OUTPUT + 1),
        )
        assert effect.output_preview == "x" * PREVIEW_LENGTH_TOOL_OUTPUT + "..."
        assert len(effect.output_preview) == PREVIEW_LENGTH_TOOL_OUTPUT + 3

    def test_prompt_boundary(self):
        """PromptSent uses PREVIEW_LENGTH_PROMPT boundary."""
        effect = PromptSent(
            system_prompt="x" * PREVIEW_LENGTH_PROMPT,
            user_prompt="y" * (PREVIEW_LENGTH_PROMPT + 1),
        )
        assert effect.system_prompt_preview == "x" * PREVIEW_LENGTH_PROMPT  # No truncation
        assert effect.user_prompt_preview == "y" * PREVIEW_LENGTH_PROMPT + "..."  # Truncated

    def test_step_outputs_boundary(self):
        """StepCompleted uses PREVIEW_LENGTH_STEP_SUMMARY boundary."""
        effect = StepCompleted(
            step_name="test",
            parent_task="Task",
            outputs="x" * PREVIEW_LENGTH_STEP_SUMMARY,
        )
        assert effect.outputs_summary == "x" * PREVIEW_LENGTH_STEP_SUMMARY  # No truncation

        effect2 = StepCompleted(
            step_name="test",
            parent_task="Task",
            outputs="x" * (PREVIEW_LENGTH_STEP_SUMMARY + 1),
        )
        assert effect2.outputs_summary == "x" * PREVIEW_LENGTH_STEP_SUMMARY + "..."


class TestBackwardCompatibility:
    """Tests for loading effects serialized with old field names.

    Pydantic v2 ignores unknown fields (extra='ignore' is default).
    Old field names are silently discarded, new fields default to empty.
    """

    def test_old_tool_call_completed_loads(self):
        """Old ToolCallCompleted with output_preview loads (field ignored)."""
        old_data = {
            "effect_type": "tool_call_completed",
            "tool_call_id": "tc_001",
            "tool_name": "Read",
            "output_preview": "old preview",  # Old field name - ignored
            "success": True,
        }
        effect = effect_from_dict(old_data)
        # output defaults to "", so preview is also ""
        assert effect.output == ""
        assert effect.output_preview == ""

    def test_old_prompt_sent_loads(self):
        """Old PromptSent with preview fields loads (fields ignored)."""
        old_data = {
            "effect_type": "prompt_sent",
            "system_prompt_preview": "old system",  # Old field name - ignored
            "user_prompt_preview": "old user",  # Old field name - ignored
            "total_tokens": 10,
        }
        effect = effect_from_dict(old_data)
        # New fields default to ""
        assert effect.system_prompt == ""
        assert effect.user_prompt == ""
        assert effect.system_prompt_preview == ""
        assert effect.user_prompt_preview == ""

    def test_old_bash_command_loads(self):
        """Old bash_command payloads now fall back to the base Effect in core-only decode."""
        old_data = {
            "effect_type": "bash_command",
            "command": "ls",
            "output": "files",
            "output_preview": "files",  # Old stored field - now ignored
            "exit_code": 0,
        }
        effect = effect_from_dict(old_data)
        assert type(effect).__name__ == "Effect"
        assert effect.effect_type == "bash_command"

    def test_old_step_completed_loads(self):
        """Old StepCompleted with outputs_summary loads (field ignored)."""
        old_data = {
            "effect_type": "step_completed",
            "step_name": "analyze",
            "parent_task": "Task",
            "outputs_summary": "old summary",  # Old field name - ignored
            "duration_ms": 100.0,
        }
        effect = effect_from_dict(old_data)
        # outputs defaults to None, so summary is ""
        assert effect.outputs is None
        assert effect.outputs_summary == ""


# =============================================================================
# Phase 1b: FileRead, ArtifactWritten, ExternalAPICall
# =============================================================================


class TestFileRead:
    """Tests for FileRead with full content (Phase 1b)."""

    def test_content_stored_in_full(self):
        from shepherd_core.effects import FileRead

        effect = FileRead(
            path="/test/file.txt",
            content="Hello, world!",
            content_hash="abc123",
        )
        assert effect.content == "Hello, world!"
        assert effect.content_hash == "abc123"
        assert effect.content_truncated is False

    def test_content_preview_computed(self):
        from shepherd_core.effects import FileRead

        effect = FileRead(path="/test/file.txt", content="Short content")
        assert effect.content_preview == "Short content"

    def test_content_preview_truncates_long_content(self):
        from shepherd_core.effects import PREVIEW_LENGTH_FILE_CONTENT, FileRead

        long_content = "x" * 1000
        effect = FileRead(path="/test/big.txt", content=long_content)
        assert len(effect.content_preview) == PREVIEW_LENGTH_FILE_CONTENT + 3
        assert effect.content_preview.endswith("...")
        assert effect.content == long_content

    def test_serialization_excludes_preview(self):
        from shepherd_core.effects import FileRead

        effect = FileRead(path="/test/file.txt", content="content")
        data = effect.model_dump()
        assert "content" in data
        assert "content_preview" not in data

    def test_round_trip_serialization(self):
        from shepherd_core.effects import FileRead

        original = FileRead(
            path="/test/file.txt",
            content="full file content",
            content_hash="sha256hash",
            content_truncated=False,
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert restored.content == original.content
        assert restored.content_hash == original.content_hash
        assert restored.content_truncated == original.content_truncated
        assert restored.content_preview == original.content_preview


class TestArtifactWritten:
    """Tests for ArtifactWritten with full content (Phase 1b)."""

    def test_content_stored_in_full(self):
        from shepherd_core.effects import ArtifactWritten

        effect = ArtifactWritten(
            filename="output.txt",
            path="/tmp/output.txt",
            content_type="text",
            size_bytes=100,
            field_name="result",
            content="Artifact content",
            content_hash="abc123",
        )
        assert effect.content == "Artifact content"
        assert effect.content_hash == "abc123"
        assert effect.content_truncated is False

    def test_content_preview_computed(self):
        from shepherd_core.effects import ArtifactWritten

        effect = ArtifactWritten(
            filename="test.txt",
            path="/tmp/test.txt",
            content="Short artifact",
        )
        assert effect.content_preview == "Short artifact"

    def test_content_preview_truncates_long_content(self):
        from shepherd_core.effects import PREVIEW_LENGTH_ARTIFACT, ArtifactWritten

        long_content = "y" * 1000
        effect = ArtifactWritten(
            filename="big.txt",
            path="/tmp/big.txt",
            content=long_content,
        )
        assert len(effect.content_preview) == PREVIEW_LENGTH_ARTIFACT + 3
        assert effect.content_preview.endswith("...")
        assert effect.content == long_content

    def test_serialization_excludes_preview(self):
        from shepherd_core.effects import ArtifactWritten

        effect = ArtifactWritten(filename="test.txt", path="/tmp/test.txt", content="data")
        data = effect.model_dump()
        assert "content" in data
        assert "content_preview" not in data

    def test_round_trip_serialization(self):
        from shepherd_core.effects import ArtifactWritten

        original = ArtifactWritten(
            filename="output.json",
            path="/tmp/output.json",
            content_type="json",
            size_bytes=50,
            field_name="config",
            content='{"key": "value"}',
            content_hash="sha256hash",
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert restored.content == original.content
        assert restored.content_hash == original.content_hash
        assert restored.content_preview == original.content_preview


class TestExternalAPICall:
    """Tests for ExternalAPICall with request/response bodies (Phase 1b)."""

    def test_bodies_stored_in_full(self):
        from shepherd_core.effects import ExternalAPICall

        effect = ExternalAPICall(
            service="stripe",
            endpoint="/v1/charges",
            method="POST",
            status_code=200,
            request_body='{"amount": 1000}',
            response_body='{"id": "ch_123"}',
            response_headers={"content-type": "application/json"},
            duration_ms=150.5,
        )
        assert effect.request_body == '{"amount": 1000}'
        assert effect.response_body == '{"id": "ch_123"}'
        assert effect.response_headers == {"content-type": "application/json"}
        assert effect.duration_ms == 150.5

    def test_preview_properties_computed(self):
        from shepherd_core.effects import ExternalAPICall

        effect = ExternalAPICall(
            service="api",
            request_body="short request",
            response_body="short response",
        )
        assert effect.request_preview == "short request"
        assert effect.response_preview == "short response"

    def test_preview_truncates_long_bodies(self):
        from shepherd_core.effects import PREVIEW_LENGTH_API_BODY, ExternalAPICall

        long_body = "z" * 1000
        effect = ExternalAPICall(
            service="api",
            request_body=long_body,
            response_body=long_body,
        )
        assert len(effect.request_preview) == PREVIEW_LENGTH_API_BODY + 3
        assert len(effect.response_preview) == PREVIEW_LENGTH_API_BODY + 3
        assert effect.request_preview.endswith("...")
        assert effect.response_preview.endswith("...")

    def test_serialization_excludes_previews(self):
        from shepherd_core.effects import ExternalAPICall

        effect = ExternalAPICall(
            service="api",
            request_body="req",
            response_body="res",
        )
        data = effect.model_dump()
        assert "request_body" in data
        assert "response_body" in data
        assert "request_preview" not in data
        assert "response_preview" not in data

    def test_round_trip_serialization(self):
        from shepherd_core.effects import ExternalAPICall

        original = ExternalAPICall(
            service="github",
            endpoint="/repos/owner/repo",
            method="GET",
            status_code=200,
            request_body="",
            response_body='{"name": "repo"}',
            response_headers={"x-ratelimit-remaining": "59"},
            duration_ms=200.0,
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert restored.request_body == original.request_body
        assert restored.response_body == original.response_body
        assert restored.response_headers == original.response_headers
        assert restored.duration_ms == original.duration_ms


class TestTruncateWithHash:
    """Tests for the truncate_with_hash helper function."""

    def test_small_content_not_truncated(self):
        from shepherd_core.effects import truncate_with_hash

        content = "Hello, world!"
        result, hash_val, truncated = truncate_with_hash(content)
        assert result == content
        assert truncated is False
        assert len(hash_val) == 64  # SHA256 hex

    def test_at_threshold_not_truncated(self):
        from shepherd_core.effects import MAX_CONTENT_SIZE, truncate_with_hash

        content = "x" * MAX_CONTENT_SIZE
        result, _hash_val, truncated = truncate_with_hash(content)
        assert result == content
        assert truncated is False
        assert len(result) == MAX_CONTENT_SIZE

    def test_over_threshold_truncated(self):
        from shepherd_core.effects import MAX_CONTENT_SIZE, truncate_with_hash

        content = "x" * (MAX_CONTENT_SIZE + 100_000)
        result, _hash_val, truncated = truncate_with_hash(content)
        assert truncated is True
        assert len(result) < len(content)
        assert "bytes truncated" in result

    def test_hash_computed_from_full_content(self):
        """Hash should be computed from full content, not truncated."""
        import hashlib

        from shepherd_core.effects import MAX_CONTENT_SIZE, truncate_with_hash

        content = "x" * (MAX_CONTENT_SIZE + 100_000)
        expected_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        _result, hash_val, _truncated = truncate_with_hash(content)
        assert hash_val == expected_hash

    def test_head_and_tail_preserved(self):
        from shepherd_core.effects import truncate_with_hash

        # Create content with distinct head and tail
        head = "HEAD" * 100_000  # 400KB
        tail = "TAIL" * 100_000  # 400KB
        middle = "M" * 500_000  # 500KB
        content = head + middle + tail  # 1.3MB total

        result, _hash_val, truncated = truncate_with_hash(content)
        assert truncated is True
        assert result.startswith("HEAD")
        assert result.endswith("TAIL")

    def test_unicode_content_handled(self):
        from shepherd_core.effects import truncate_with_hash

        content = "Hello 🌍 世界! " * 100
        result, hash_val, truncated = truncate_with_hash(content)
        assert truncated is False
        assert result == content
        assert len(hash_val) == 64


class TestLifecyclePhaseFailed:
    """Tests for LifecyclePhaseFailed registry and roundtrip serialization."""

    def test_registered_in_effect_types(self):
        from shepherd_core.effects import EFFECT_TYPES, LifecyclePhaseFailed

        assert "lifecycle_phase_failed" in EFFECT_TYPES
        assert EFFECT_TYPES["lifecycle_phase_failed"] is LifecyclePhaseFailed

    def test_round_trip_serialization(self):
        from shepherd_core.effects import LifecyclePhaseFailed, effect_from_dict

        original = LifecyclePhaseFailed(
            phase="execute",
            duration_ms=42.0,
            error_type="ExecutionError",
            error_message="boom",
        )
        data = original.model_dump()
        restored = effect_from_dict(data)
        assert isinstance(restored, LifecyclePhaseFailed)
        assert restored.phase == "execute"
        assert restored.duration_ms == 42.0
        assert restored.error_type == "ExecutionError"
        assert restored.error_message == "boom"
