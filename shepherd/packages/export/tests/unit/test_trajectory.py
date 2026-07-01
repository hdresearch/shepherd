"""Tests for Tier 3: Lossless trajectory export/import."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal

from shepherd_coding.contexts.effects import PRMerged
from shepherd_core.effects import (
    KERNEL_EFFECT_REGISTRY,
    AgentMessage,
    Effect,
    PromptSent,
    TaskCompleted,
    TaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from shepherd_core.scope.stream import Stream
from shepherd_export._manifest import (
    ScopeNode,
    TrajectoryManifest,
    manifest_from_dict,
    manifest_to_dict,
)
from shepherd_export.trajectory import ScopeInfo, from_trajectory, to_trajectory
from shepherd_runtime.effects import compose_effect_registry


def _sample_stream() -> Stream:
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="TestTask"))
    stream = stream.append(PromptSent(user_prompt="Do something"))
    stream = stream.append(AgentMessage(content="OK"))
    stream = stream.append(ToolCallStarted(tool_call_id="tc1", tool_name="bash", params={"command": "echo hi"}))
    stream = stream.append(ToolCallCompleted(tool_call_id="tc1", tool_name="bash", output="hi"))
    return stream.append(TaskCompleted(task_name="TestTask", duration_ms=50.0))


class TestManifestSerialization:
    def test_round_trip(self):
        manifest = TrajectoryManifest(
            version="1.0",
            root_scope_id="root",
            scopes=(
                ScopeNode(
                    scope_id="root",
                    parent_scope_id=None,
                    stream_file="scope_root.jsonl",
                    status="active",
                    depth=0,
                ),
                ScopeNode(
                    scope_id="fork1",
                    parent_scope_id="root",
                    stream_file="scope_fork1.jsonl",
                    status="discarded",
                    depth=1,
                    metadata={"reason": "failed"},
                ),
            ),
            created_at="2024-01-01T00:00:00Z",
            metadata={"task": "test"},
        )

        manifest_dict = manifest_to_dict(manifest)
        restored = manifest_from_dict(manifest_dict)

        assert restored.version == "1.0"
        assert restored.root_scope_id == "root"
        assert len(restored.scopes) == 2
        assert restored.scopes[1].status == "discarded"
        assert restored.scopes[1].metadata == {"reason": "failed"}
        assert restored.created_at == "2024-01-01T00:00:00Z"

    def test_json_serializable(self):
        manifest = TrajectoryManifest(
            scopes=(ScopeNode(scope_id="root", parent_scope_id=None, stream_file="root.jsonl", status="active"),),
        )
        json.dumps(manifest_to_dict(manifest))

    def test_empty_manifest(self):
        manifest = TrajectoryManifest()
        restored = manifest_from_dict(manifest_to_dict(manifest))
        assert len(restored.scopes) == 0


class TestSingleScopeExport:
    def test_creates_directory(self):
        stream = _sample_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "test")
            assert output_dir.exists()
            assert (output_dir / "manifest.json").exists()
            assert (output_dir / "scope_root.jsonl").exists()

    def test_manifest_content(self):
        stream = _sample_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "test")
            manifest = json.loads((output_dir / "manifest.json").read_text())

            assert manifest["root_scope_id"] == "root"
            assert len(manifest["scopes"]) == 1
            assert manifest["scopes"][0]["scope_id"] == "root"
            assert manifest["scopes"][0]["status"] == "active"

    def test_jsonl_content(self):
        stream = _sample_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "test")
            jsonl_path = output_dir / "scope_root.jsonl"

            lines = jsonl_path.read_text().strip().split("\n")
            assert len(lines) == len(stream)

            first = json.loads(lines[0])
            assert "effect" in first
            assert "sequence" in first
            assert first["effect"]["effect_type"] == "task_started"

    def test_round_trip(self):
        original = _sample_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(original, Path(tmpdir) / "test")
            result = from_trajectory(output_dir)

            assert len(result.root_stream) == len(original)
            for orig, imported in zip(original, result.root_stream, strict=False):
                assert orig.effect.effect_type == imported.effect.effect_type

    def test_preserves_tool_call_fields(self):
        original = _sample_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(original, Path(tmpdir) / "test")
            result = from_trajectory(output_dir)

            tc_layers = [layer for layer in result.root_stream if layer.effect.effect_type == "tool_call_started"]
            assert len(tc_layers) == 1
            assert tc_layers[0].effect.tool_name == "bash"
            assert tc_layers[0].effect.tool_call_id == "tc1"
            assert tc_layers[0].effect.params == {"command": "echo hi"}

    def test_metadata_in_manifest(self):
        stream = _sample_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "test", metadata={"task": "demo", "version": 1})
            manifest = json.loads((output_dir / "manifest.json").read_text())
            assert manifest["metadata"]["task"] == "demo"
            assert manifest["metadata"]["version"] == 1

    def test_registry_argument_controls_custom_effect_decode(self):
        class TrajectoryOnlyEffect(Effect):
            effect_type: Literal["trajectory_only_effect"] = "trajectory_only_effect"
            payload: str = ""

        stream = Stream().append(TrajectoryOnlyEffect(payload="x"))
        registry = KERNEL_EFFECT_REGISTRY.extend({"trajectory_only_effect": TrajectoryOnlyEffect})

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "custom")
            result = from_trajectory(output_dir, registry=registry)

        assert isinstance(result.root_stream[0].effect, TrajectoryOnlyEffect)
        assert result.root_stream[0].effect.payload == "x"

    def test_kernel_default_decode_stays_fail_closed_for_contributor_effects(self):
        stream = Stream().append(
            PRMerged(pr_number=7, repo="shepherd/repo", merge_commit_sha="abc", merge_method="squash")
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "contributor")
            result = from_trajectory(output_dir)

        assert type(result.root_stream[0].effect) is Effect
        assert result.root_stream[0].effect.effect_type == "pr_merged"

    def test_registry_argument_decodes_contributor_effects_with_runtime_composition(self):
        stream = Stream().append(
            PRMerged(pr_number=7, repo="shepherd/repo", merge_commit_sha="abc", merge_method="squash")
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "contributor")
            result = from_trajectory(output_dir, registry=compose_effect_registry())

        assert isinstance(result.root_stream[0].effect, PRMerged)
        assert result.root_stream[0].effect.pr_number == 7


class TestMultiScopeExport:
    def _make_scope_tree(self) -> tuple[Stream, dict[str, ScopeInfo]]:
        main_stream = _sample_stream()

        fork_stream = Stream()
        fork_stream = fork_stream.append(TaskStarted(task_name="Alternative"))
        fork_stream = fork_stream.append(AgentMessage(content="Trying backup approach"))

        discarded_stream = Stream()
        discarded_stream = discarded_stream.append(TaskStarted(task_name="BadIdea"))
        discarded_stream = discarded_stream.append(AgentMessage(content="This won't work"))

        scope_tree = {
            "root": ScopeInfo(scope_id="root", parent_scope_id=None, stream=main_stream, status="active"),
            "fork1": ScopeInfo(scope_id="fork1", parent_scope_id="root", stream=fork_stream, status="merged"),
            "discarded": ScopeInfo(
                scope_id="discarded",
                parent_scope_id="root",
                stream=discarded_stream,
                status="discarded",
                metadata={"reason": "failed approach"},
            ),
        }
        return main_stream, scope_tree

    def test_creates_per_scope_files(self):
        main_stream, scope_tree = self._make_scope_tree()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(main_stream, Path(tmpdir) / "multi", scope_tree=scope_tree)

            assert (output_dir / "manifest.json").exists()
            assert (output_dir / "scope_root.jsonl").exists()
            assert (output_dir / "scope_fork1.jsonl").exists()
            assert (output_dir / "scope_discarded.jsonl").exists()

    def test_manifest_has_all_scopes(self):
        main_stream, scope_tree = self._make_scope_tree()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(main_stream, Path(tmpdir) / "multi", scope_tree=scope_tree)
            manifest = json.loads((output_dir / "manifest.json").read_text())

            scope_ids = {scope["scope_id"] for scope in manifest["scopes"]}
            assert scope_ids == {"root", "fork1", "discarded"}

            statuses = {scope["scope_id"]: scope["status"] for scope in manifest["scopes"]}
            assert statuses["root"] == "active"
            assert statuses["fork1"] == "merged"
            assert statuses["discarded"] == "discarded"

    def test_round_trip_preserves_all_scopes(self):
        main_stream, scope_tree = self._make_scope_tree()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(main_stream, Path(tmpdir) / "multi", scope_tree=scope_tree)
            result = from_trajectory(output_dir)

            assert len(result.scope_streams) == 3
            assert "root" in result.scope_streams
            assert "fork1" in result.scope_streams
            assert "discarded" in result.scope_streams

    def test_discarded_branch_preserved(self):
        main_stream, scope_tree = self._make_scope_tree()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(main_stream, Path(tmpdir) / "multi", scope_tree=scope_tree)
            result = from_trajectory(output_dir)

            discarded = result.scope_streams["discarded"]
            assert len(discarded) == 2
            assert discarded[0].effect.effect_type == "task_started"
            assert discarded[0].effect.task_name == "BadIdea"

    def test_scope_metadata_preserved(self):
        main_stream, scope_tree = self._make_scope_tree()
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(main_stream, Path(tmpdir) / "multi", scope_tree=scope_tree)
            manifest = json.loads((output_dir / "manifest.json").read_text())

            discarded_scope = next(scope for scope in manifest["scopes"] if scope["scope_id"] == "discarded")
            assert discarded_scope["metadata"]["reason"] == "failed approach"


class TestEmptyStream:
    def test_empty_export_import(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(Stream(), Path(tmpdir) / "empty")
            result = from_trajectory(output_dir)
            assert len(result.root_stream) == 0


class TestEdgeCases:
    def test_scope_id_with_special_chars(self):
        stream = _sample_stream()
        scope_tree = {
            "root/main": ScopeInfo(scope_id="root/main", parent_scope_id=None, stream=stream, status="active"),
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = to_trajectory(stream, Path(tmpdir) / "special", scope_tree=scope_tree)
            assert (output_dir / "scope_root_main.jsonl").exists()

    def test_double_export_overwrites(self):
        stream = _sample_stream()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "overwrite"
            to_trajectory(stream, path)
            to_trajectory(stream, path)
            result = from_trajectory(path)
            assert len(result.root_stream) == len(stream)
