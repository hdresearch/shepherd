"""Subprocess entry point: run the Claude Agent SDK to edit cwd per AGENT_INSTRUCTION.

Exec'd as the command under ``vcs-core session exec --capture`` (cwd = the
overlay mount), so the agent's file edits land in the overlay and are captured
by vcs-core. The instruction is read from ``AGENT_INSTRUCTION``. Output content
is nondeterministic (a live model); callers assert on the deterministic outcome
(the named file lands in / is reverted from ground), not the prose.

``AGENT_ALLOWED_TOOLS`` (JSON list), when set, is the may=-pruned read-only tool
allowlist (Rung A capability pruning). **Important SDK semantics:** the SDK's
``allowed_tools`` is only an *auto-approve* list — unlisted tools still exist and
fall through to ``permission_mode`` (and ``acceptEdits`` auto-approves writes!).
So an allowlist alone does NOT make an agent read-only. To genuinely restrict, a
restricted run also (a) **hard-removes** the write tools via ``disallowed_tools``
(bare names drop the tool from the request — true capability attenuation) and
(b) denies anything not pre-approved via ``permission_mode="dontAsk"``. Unset ⇒
the default full toolset under ``acceptEdits``.

Run:  AGENT_INSTRUCTION="..." python -m shepherd._claude_agent_runner
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_DEFAULT_TOOLS = ["Read", "Write", "Edit"]
# Workspace-mutating tools (directly or via shell). Hard-removed from a restricted
# agent's request so it holds no write capability at all.
_WRITE_TOOLS = ["Write", "Edit", "Bash", "NotebookEdit"]


def _tool_policy() -> tuple[list[str], list[str], str]:
    """Return (allowed_tools, disallowed_tools, permission_mode) from the env.

    SDK-free so the policy can be unit-tested without the agent SDK installed.
    """
    raw = os.environ.get("AGENT_ALLOWED_TOOLS")
    if not raw:
        # Unrestricted: full toolset, auto-accept edits.
        return list(_DEFAULT_TOOLS), [], "acceptEdits"
    # Restricted (read-only): approve only the read tools, hard-remove the write
    # tools, and deny anything else outright (defense in depth).
    allowed = [str(t) for t in json.loads(raw)]
    return allowed, list(_WRITE_TOOLS), "dontAsk"


async def _run() -> int:
    from claude_agent_sdk import ClaudeAgentOptions, query

    instruction = os.environ.get("AGENT_INSTRUCTION", "").strip()
    if not instruction:
        print("AGENT_INSTRUCTION is not set", file=sys.stderr)  # noqa: T201 - subprocess stdio is the interface
        return 1

    allowed, disallowed, permission_mode = _tool_policy()
    options = ClaudeAgentOptions(
        cwd=str(Path.cwd()),
        permission_mode=permission_mode,
        allowed_tools=allowed,
        disallowed_tools=disallowed,
    )
    ok = False
    async for message in query(prompt=instruction, options=options):
        if type(message).__name__ == "ResultMessage":
            ok = not getattr(message, "is_error", False)
    print("claude agent run complete" if ok else "claude agent run failed")  # noqa: T201 - summary read by handler
    return 0 if ok else 1


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    sys.exit(main())
