"""Unit tests for the Claude agent runner's tool policy (SDK-free).

These lock the read-only enforcement recipe that the live e2e proved necessary:
the SDK's ``allowed_tools`` is an auto-approve list, not a capability allowlist,
so a restricted run must also hard-remove write tools (``disallowed_tools``) and
deny-by-default (``permission_mode="dontAsk"``).
"""

from __future__ import annotations

import json

from shepherd._claude_agent_runner import _tool_policy


def test_tool_policy_unrestricted_when_no_env(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_ALLOWED_TOOLS", raising=False)
    allowed, disallowed, mode = _tool_policy()
    assert allowed == ["Read", "Write", "Edit"]
    assert disallowed == []
    assert mode == "acceptEdits"


def test_tool_policy_restricted_hard_removes_writes_and_denies_by_default(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_ALLOWED_TOOLS", json.dumps(["Read", "Grep", "Glob"]))
    allowed, disallowed, mode = _tool_policy()

    assert allowed == ["Read", "Grep", "Glob"]
    # Write tools are hard-removed (allowlist alone is only auto-approve).
    for write_tool in ("Write", "Edit", "Bash", "NotebookEdit"):
        assert write_tool in disallowed
    # Deny-by-default backstop: anything not pre-approved is refused, no prompt.
    assert mode == "dontAsk"
    # The read-only allowlist must not itself contain a write/exec tool.
    assert not (set(allowed) & set(disallowed))
