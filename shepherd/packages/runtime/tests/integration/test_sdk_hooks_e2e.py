"""E2E test: runtime StackHooks wiring through the real Claude Agent SDK."""

from __future__ import annotations

import os
import tempfile
from typing import Any

import pytest
from shepherd_runtime.device.container.stack_hooks import StackHooks


def _has_api_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _has_claude_sdk() -> bool:
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        return False
    return True


def _has_claude_cli() -> bool:
    import shutil

    return shutil.which("claude") is not None


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.integration,
    pytest.mark.skipif(not _has_api_key(), reason="ANTHROPIC_API_KEY not set"),
    pytest.mark.skipif(not _has_claude_sdk(), reason="claude-agent-sdk not installed"),
    pytest.mark.skipif(not _has_claude_cli(), reason="claude CLI not found on PATH"),
]


class _OverlayStub:
    def __init__(self) -> None:
        self.pre_ids: list[str] = []
        self.post_ids: list[str] = []
        self.merge_failed = False

    def push_layer(self, tool_use_id: str) -> None:
        self.pre_ids.append(tool_use_id)

    def cleanup_partial(self) -> None:
        return None

    def pop_and_merge(self, tool_use_id: str) -> list[dict[str, Any]]:
        self.post_ids.append(tool_use_id)
        return []


class _CollectorStub:
    def __init__(self) -> None:
        self.effects: list[Any] = []

    def emit(self, effect: Any) -> None:
        self.effects.append(effect)


@pytest.mark.asyncio
async def test_sdk_hooks_fire_with_tool_use_id() -> None:
    """StackHooks.as_hooks_dict() must survive real SDK hook registration."""
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    from claude_agent_sdk.types import ResultMessage

    overlay = _OverlayStub()
    collector = _CollectorStub()
    hooks = StackHooks(overlay, collector).as_hooks_dict()

    with tempfile.TemporaryDirectory(prefix="sdk-hooks-e2e-") as tmpdir:
        options = ClaudeAgentOptions(
            cwd=tmpdir,
            permission_mode="bypassPermissions",
            max_turns=5,
            model="claude-sonnet-4-20250514",
            allowed_tools=["Write", "Read", "Bash", "Edit", "Glob", "Grep"],
            hooks=hooks,
        )

        client = ClaudeSDKClient(options)
        prompt_text = (
            f"Use the Write tool to create a file at {tmpdir}/hello.py "
            "with this content:\nprint('hello world')\n\n"
            f"Then use the Bash tool to run: cat {tmpdir}/hello.py\n\n"
            "You MUST use Write and Bash tools. Do not just respond with text."
        )

        async def prompt_stream():
            yield {
                "type": "user",
                "message": {"role": "user", "content": prompt_text},
                "parent_tool_use_id": None,
                "session_id": "default",
            }

        await client.connect(prompt=prompt_stream())

        async for message in client.receive_messages():
            if isinstance(message, ResultMessage):
                break

    assert overlay.pre_ids, "Expected at least one PreToolUse callback"
    assert overlay.post_ids, "Expected at least one PostToolUse callback"
    assert all(tool_use_id is not None for tool_use_id in overlay.pre_ids)
    assert all(tool_use_id is not None for tool_use_id in overlay.post_ids)
    assert set(overlay.pre_ids) == set(overlay.post_ids)
