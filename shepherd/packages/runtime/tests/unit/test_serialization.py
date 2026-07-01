"""Runtime serialization tests for composed effect registries."""

from shepherd_contexts.workspace.effects import WorkspacePatchCaptured
from shepherd_core.effects import DiffPatch, ToolCallCompleted, ToolCallStarted, effect_from_dict
from shepherd_core.types import ExecutionResult, ToolCall, ToolResult
from shepherd_runtime.effects import compose_effect_registry


class TestRuntimeEffectSerialization:
    """Tests for runtime/context effect serialization."""

    def test_effect_with_nested_diff_patch(self):
        patch = DiffPatch.from_diff(
            patch="diff --git a/file.py b/file.py\n+new line",
            files=["file.py"],
            source_step="FixBug",
        )

        original = WorkspacePatchCaptured(
            task_name="FixBug",
            provider_id="claude-sonnet",
            context_id="workspace:/repo",
            files_changed=("file.py",),
            patch_hash=patch.sha256 or "",
            patch_size_bytes=len(patch.patch),
            patch=patch,
        )

        data = original.model_dump()

        assert isinstance(data["patch"], dict)
        assert data["patch"]["patch"] == "diff --git a/file.py b/file.py\n+new line\n"
        assert data["patch"]["files_changed"] == ("file.py",)

        restored = effect_from_dict(data, registry=compose_effect_registry())

        assert isinstance(restored, WorkspacePatchCaptured)
        assert isinstance(restored.patch, DiffPatch)
        assert restored.patch.patch == patch.patch
        assert restored.patch.files_changed == ("file.py",)

    def test_cache_entry_round_trip(self):
        result = ExecutionResult(
            success=True,
            output_text="Fixed the bug by updating the validation logic.",
            tool_calls=(
                ToolCall(id="tc_1", name="Read", params={"file_path": "/auth.py"}),
                ToolCall(id="tc_2", name="Edit", params={"file_path": "/auth.py", "old": "x", "new": "y"}),
            ),
            tool_results=(
                ToolResult(tool_call_id="tc_1", success=True, output="def validate():..."),
                ToolResult(tool_call_id="tc_2", success=True, output="Edited"),
            ),
            session_id="sess_abc",
            metadata={"model": "claude-sonnet-4"},
        )

        effects = [
            ToolCallStarted(task_name="FixBug", provider_id="claude", tool_call_id="tc_1", tool_name="Read"),
            ToolCallCompleted(
                task_name="FixBug", provider_id="claude", tool_call_id="tc_1", tool_name="Read", success=True
            ),
            WorkspacePatchCaptured(
                task_name="FixBug",
                provider_id="claude",
                context_id="workspace:/repo",
                files_changed=("/auth.py",),
                patch_hash="abc123",
                patch_size_bytes=50,
                patch=DiffPatch.from_diff("diff content", ["/auth.py"], "FixBug"),
            ),
        ]

        cache_entry = {
            "result": result.model_dump(),
            "effects": [effect.model_dump() for effect in effects],
            "timestamp": 1234567890.0,
        }

        restored_result = ExecutionResult.model_validate(cache_entry["result"])
        registry = compose_effect_registry()
        restored_effects = [effect_from_dict(effect, registry=registry) for effect in cache_entry["effects"]]

        assert restored_result == result
        assert isinstance(restored_result.tool_calls[0], ToolCall)
        assert len(restored_effects) == 3
        assert isinstance(restored_effects[0], ToolCallStarted)
        assert isinstance(restored_effects[2], WorkspacePatchCaptured)
        assert isinstance(restored_effects[2].patch, DiffPatch)
