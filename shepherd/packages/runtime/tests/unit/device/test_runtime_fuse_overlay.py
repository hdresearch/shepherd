"""Focused tests for the runtime-owned fuse-overlay and hook helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestRuntimeFuseOverlayManager:
    @pytest.fixture
    def manager(self, tmp_path):
        from shepherd_runtime.device.container.fuse_overlay import FuseOverlayManager

        mgr = FuseOverlayManager()
        mgr.WORKSPACE = tmp_path / "workspace"
        mgr.WORKSPACE_RO = tmp_path / "workspace-ro"
        mgr.OVERLAYS_ROOT = tmp_path / "overlays"
        mgr._accumulated = mgr.OVERLAYS_ROOT / "accumulated"
        mgr._work = mgr.OVERLAYS_ROOT / "work"
        mgr._lower_layers = [mgr.WORKSPACE_RO]

        mgr.WORKSPACE_RO.mkdir(parents=True)
        mgr._accumulated.mkdir(parents=True)
        (mgr.WORKSPACE_RO / "baseline.txt").write_text("original\n")

        return mgr

    def test_setup_creates_directories(self, manager) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="v1.13", stderr="")
            manager.setup()

        assert manager._accumulated.exists()
        assert manager._work.exists()
        assert manager._mounted

    def test_setup_fails_without_workspace_ro(self, manager) -> None:
        import shutil

        shutil.rmtree(manager.WORKSPACE_RO)

        with pytest.raises(RuntimeError, match="workspace-ro"):
            manager.setup()

    def test_setup_accepts_explicit_lower_layers(self, manager, tmp_path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="v1.13", stderr="")
            manager.setup(lower_layers=[parent, manager.WORKSPACE_RO])

        assert manager._lower_layers == [parent, manager.WORKSPACE_RO]

    def test_pop_and_merge_returns_effects(self, manager, tmp_path) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            manager._mounted = True

            tool_dir = manager.OVERLAYS_ROOT / "tool_1"
            tool_upper = tool_dir / "upper"
            tool_work = tool_dir / "work"
            tool_upper.mkdir(parents=True)
            tool_work.mkdir(parents=True)
            (tool_upper / "new_file.py").write_text("print('hello')\n")

            manager._tool_counter = 1
            manager._tool_dir = tool_dir

            effects = manager.pop_and_merge("toolu_abc")

        assert len(effects) == 1
        assert effects[0]["effect_type"] == "file_create"
        assert effects[0]["path"] == "new_file.py"
        assert effects[0]["caused_by"] == "toolu_abc"

    def test_extract_whiteout_had_content_from_workspace_ro(self, manager, tmp_path) -> None:
        (manager.WORKSPACE_RO / "deleted.py").write_text("original content")

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / ".wh.deleted.py").write_text("")

        effects = manager._extract_effects(upper, "toolu_delete")

        assert len(effects) == 1
        assert effects[0]["effect_type"] == "file_delete"
        assert effects[0]["path"] == "deleted.py"
        assert effects[0]["had_content"] == "original content"

    def test_extract_whiteout_had_content_from_accumulated(self, manager, tmp_path) -> None:
        (manager._accumulated / "prior.py").write_text("prior tool content")

        upper = tmp_path / "upper"
        upper.mkdir()
        (upper / ".wh.prior.py").write_text("")

        effects = manager._extract_effects(upper, "toolu_delete")

        assert len(effects) == 1
        assert effects[0]["effect_type"] == "file_delete"
        assert effects[0]["had_content"] == "prior tool content"

    def test_read_lower_content_prefers_highest_priority_parent_layer(self, manager, tmp_path) -> None:
        parent_new = tmp_path / "parent-new"
        parent_old = tmp_path / "parent-old"
        parent_new.mkdir()
        parent_old.mkdir()
        (parent_new / "shared.py").write_text("newest")
        (parent_old / "shared.py").write_text("older")
        manager._lower_layers = [parent_new, parent_old, manager.WORKSPACE_RO]

        assert manager._read_lower_content("shared.py") == "newest"

    def test_existed_in_lower_checks_all_parent_layers(self, manager, tmp_path) -> None:
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / "nested.txt").write_text("content")
        manager._lower_layers = [parent, manager.WORKSPACE_RO]

        assert manager._existed_in_lower(Path("nested.txt")) is True

    def test_merge_removes_shadowed_file_for_prefix_whiteout(self, manager, tmp_path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()

        (dst / "target.py").write_text("should be removed")
        (src / ".wh.target.py").write_text("")

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            manager._merge(src, dst)

        assert not (dst / "target.py").exists()


class TestRuntimeStackHooks:
    @pytest.fixture
    def mock_overlay(self):
        mgr = MagicMock()
        mgr.merge_failed = False
        mgr.pop_and_merge.return_value = [
            {"effect_type": "file_create", "path": "new.py", "content": "hello", "caused_by": "toolu_1"},
        ]
        return mgr

    @pytest.fixture
    def mock_collector(self):
        return MagicMock()

    @pytest.fixture
    def hooks(self, mock_overlay, mock_collector):
        from shepherd_runtime.device.container.stack_hooks import StackHooks

        return StackHooks(mock_overlay, mock_collector)

    @pytest.mark.asyncio
    async def test_pre_tool_use_mutating_tool_pushes_layer(self, hooks, mock_overlay) -> None:
        result = await hooks.pre_tool_use({"tool_name": "Write"}, "toolu_1", None)

        mock_overlay.push_layer.assert_called_once_with("toolu_1")
        assert hooks._layer_active
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    @pytest.mark.asyncio
    async def test_pre_tool_use_graceful_degradation(self, hooks, mock_overlay) -> None:
        mock_overlay.push_layer.side_effect = RuntimeError("mount failed")

        result = await hooks.pre_tool_use({"tool_name": "Bash"}, "toolu_2", None)

        assert not hooks._layer_active
        mock_overlay.cleanup_partial.assert_called_once()
        assert result["hookSpecificOutput"]["permissionDecision"] == "allow"

    @pytest.mark.asyncio
    async def test_post_tool_use_emits_effects(self, hooks, mock_overlay, mock_collector) -> None:
        hooks._layer_active = True

        await hooks.post_tool_use({"tool_name": "Write"}, "toolu_1", None)

        mock_overlay.pop_and_merge.assert_called_once_with("toolu_1")
        assert mock_collector.emit.called
        assert not hooks._layer_active

    def test_as_hooks_dict_uses_hookmatcher_when_sdk_available(self, hooks) -> None:
        try:
            from claude_agent_sdk.types import HookMatcher
        except ImportError:
            pytest.skip("claude-agent-sdk not installed")

        result = hooks.as_hooks_dict()
        assert isinstance(result["PreToolUse"][0], HookMatcher)
        assert isinstance(result["PostToolUse"][0], HookMatcher)

    def test_emit_file_delete(self, hooks, mock_collector) -> None:
        from shepherd_core.effects import FileDelete

        hooks._emit_effect(
            {
                "effect_type": "file_delete",
                "path": "src/obsolete.py",
                "had_content": "old code",
                "caused_by": "toolu_del",
            }
        )

        mock_collector.emit.assert_called_once()
        effect = mock_collector.emit.call_args[0][0]
        assert isinstance(effect, FileDelete)
        assert effect.path == "src/obsolete.py"
        assert effect.had_content == "old code"
        assert effect.caused_by == "toolu_del"
