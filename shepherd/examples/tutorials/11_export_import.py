"""Example 11: Three-Tier Trajectory Export/Import.

Export and import agent trajectories in three formats:
1. **JSON** — Flat summary for logging/debugging
2. **ATIF** — Harbor-compatible format for trajectory interop
3. **Trajectory** — Lossless format preserving scope tree + discarded branches

Key concepts:
1. ``to_json()`` / ``from_json()`` for quick inspection
2. ``to_atif()`` / ``from_atif()`` for Harbor/Claude Code interop
3. ``to_trajectory()`` / ``from_trajectory()`` for lossless round-trips
4. ``from_claude_code_session()`` to import Claude Code sessions
5. Discarded branches preserved in Tier 3 exports

Prerequisites:
    Tutorial 01 (Simple Tasks) and Tutorial 08 (Advanced — fork/merge).

Run with:
    uv run python shepherd/examples/tutorials/11_export_import.py
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from shepherd_core.effects import (
    AgentMessage,
    AgentThinking,
    PromptSent,
    TaskCompleted,
    TaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from shepherd_core.scope.stream import Stream
from shepherd_export import from_json, from_trajectory, to_json, to_trajectory
from shepherd_export.trajectory import ScopeInfo


def section(title: str) -> None:
    """Print a section header for tutorial output."""
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def build_sample_stream() -> Stream:
    """Build a sample stream with realistic effects."""
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="FixSSLCert"))
    stream = stream.append(PromptSent(user_prompt="Fix the SSL certificate in /app/ssl"))
    stream = stream.append(AgentThinking(content="I need to check the current cert status first"))
    stream = stream.append(AgentMessage(content="I'll check the SSL certificate and regenerate it."))
    stream = stream.append(
        ToolCallStarted(
            tool_call_id="tc_001", tool_name="bash", params={"command": "openssl x509 -in /app/ssl/cert.pem -text"}
        )
    )
    stream = stream.append(
        ToolCallCompleted(tool_call_id="tc_001", tool_name="bash", output="Certificate expired 2024-01-01")
    )
    stream = stream.append(
        ToolCallStarted(
            tool_call_id="tc_002", tool_name="bash", params={"command": "openssl req -x509 -newkey rsa:2048 ..."}
        )
    )
    stream = stream.append(
        ToolCallCompleted(tool_call_id="tc_002", tool_name="bash", output="Certificate generated successfully")
    )
    stream = stream.append(AgentMessage(content="SSL certificate has been regenerated successfully."))
    stream = stream.append(TaskCompleted(task_name="FixSSLCert", duration_ms=4500.0))
    return stream  # noqa: RET504


# ─────────────────────────────────────────────────────────────
# Part 1: JSON Export (Tier 1)
# ─────────────────────────────────────────────────────────────

section("Part 1: JSON Export (Tier 1)")
stream = build_sample_stream()

json_output = to_json(stream)
doc = json.loads(json_output)
print(f"Total effects: {doc['total_effects']}")
print(f"Effect types: {doc['effect_types']}")
print(f"First effect: {doc['timeline'][0]['effect_type']}")

# Round-trip
imported = from_json(json_output)
print(f"\nRound-trip: {len(stream)} effects → JSON → {len(imported)} effects")
assert len(imported) == len(stream), "Round-trip should preserve effect count"

# Write to file
with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
    to_json(stream, path=f.name)
    print(f"Written to: {f.name}")
    reimported = from_json(f.name)
    assert len(reimported) == len(stream)
    print("File round-trip: OK")

# ─────────────────────────────────────────────────────────────
# Part 2: ATIF Export (Tier 2)
# ─────────────────────────────────────────────────────────────

section("Part 2: ATIF Export (Tier 2)")
try:
    from shepherd_export import from_atif, to_atif, to_atif_json

    atif_dict = to_atif(stream, agent_name="tutorial-agent", model_name="claude-sonnet-4-6")
    print(f"Schema version: {atif_dict['schema_version']}")
    print(f"Steps: {len(atif_dict['steps'])}")
    for step in atif_dict["steps"]:
        source = step["source"]
        msg_preview = str(step.get("message", ""))[:60]
        tc_count = len(step.get("tool_calls", []))
        extra = f" ({tc_count} tool calls)" if tc_count else ""
        print(f"  [{source}] {msg_preview}{extra}")

    # Round-trip through ATIF
    reimported = from_atif(atif_dict)
    print(
        f"\nATIF round-trip: {len(stream)} effects → ATIF ({len(atif_dict['steps'])} steps) → {len(reimported)} effects"
    )

    # JSON serialization
    atif_json = to_atif_json(stream)
    print(f"ATIF JSON size: {len(atif_json)} bytes")

except ImportError:
    print("Harbor not installed — skipping ATIF export demo")
    print("Install with: pip install harbor")

# ─────────────────────────────────────────────────────────────
# Part 3: Lossless Trajectory Export (Tier 3)
# ─────────────────────────────────────────────────────────────

section("Part 3: Lossless Trajectory Export (Tier 3)")

with tempfile.TemporaryDirectory() as tmpdir:
    # Single-scope export
    output_dir = to_trajectory(stream, Path(tmpdir) / "single")
    print(f"Exported to: {output_dir}")

    # Check what was written
    for f in sorted(output_dir.iterdir()):
        print(f"  {f.name} ({f.stat().st_size} bytes)")

    # Import back
    result = from_trajectory(output_dir)
    print(f"\nRoot stream: {len(result.root_stream)} effects")
    print(f"Scopes: {len(result.scope_streams)}")
    assert len(result.root_stream) == len(stream), "Lossless round-trip failed!"
    print("Single-scope round-trip: OK")

# ─────────────────────────────────────────────────────────────
# Part 4: Multi-Scope with Discarded Branch (Tier 3)
# ─────────────────────────────────────────────────────────────

section("Part 4: Fork + Discard → Lossless Export")

# Simulate a fork: main branch and a discarded alternative
main_stream = build_sample_stream()

# The discarded branch tried a different approach
discarded_stream = Stream()
discarded_stream = discarded_stream.append(TaskStarted(task_name="FixSSLCert"))
discarded_stream = discarded_stream.append(PromptSent(user_prompt="Fix the SSL certificate"))
discarded_stream = discarded_stream.append(AgentMessage(content="Let me try copying from backup..."))
discarded_stream = discarded_stream.append(
    ToolCallStarted(tool_call_id="tc_d01", tool_name="bash", params={"command": "cp /backup/cert.pem /app/ssl/"})
)
discarded_stream = discarded_stream.append(
    ToolCallCompleted(tool_call_id="tc_d01", tool_name="bash", output="Error: backup not found")
)

scope_tree = {
    "root": ScopeInfo(scope_id="root", parent_scope_id=None, stream=main_stream, status="active"),
    "fork_alternative": ScopeInfo(
        scope_id="fork_alternative",
        parent_scope_id="root",
        stream=discarded_stream,
        status="discarded",
        metadata={"reason": "backup approach failed"},
    ),
}

with tempfile.TemporaryDirectory() as tmpdir:
    output_dir = to_trajectory(
        main_stream,
        Path(tmpdir) / "forked",
        scope_tree=scope_tree,
        metadata={"task": "FixSSLCert", "approach": "regenerate vs backup"},
    )

    # Check manifest
    manifest_data = json.loads((output_dir / "manifest.json").read_text())
    print(f"Root scope: {manifest_data['root_scope_id']}")
    print(f"Scopes in manifest: {len(manifest_data['scopes'])}")
    for scope in manifest_data["scopes"]:
        print(f"  {scope['scope_id']}: {scope['status']} ({scope['stream_file']})")

    # Import and access discarded branch
    result = from_trajectory(output_dir)
    print(f"\nImported {len(result.scope_streams)} scopes:")
    for scope_id, scope_stream in result.scope_streams.items():
        print(f"  {scope_id}: {len(scope_stream)} effects")

    # Access the discarded branch
    discarded = result.scope_streams.get("fork_alternative")
    if discarded:
        print(f"\nDiscarded branch preserved with {len(discarded)} effects!")
        for layer in discarded:
            print(
                f"  [{layer.effect.effect_type}] {getattr(layer.effect, 'content', getattr(layer.effect, 'task_name', ''))}"
            )

# ─────────────────────────────────────────────────────────────
# Part 5: Result facade methods
# ─────────────────────────────────────────────────────────────

section("Part 5: Result facade (Agent.run() → export)")

from shepherd.agent import Result

result = Result(output="SSL fixed", success=True, effects=stream)

# Tier 1
json_str = result.to_json()
print(f"result.to_json() → {len(json_str)} bytes")

# Tier 3
with tempfile.TemporaryDirectory() as tmpdir:
    path = result.to_trajectory(str(Path(tmpdir) / "result_export"))
    print(f"result.to_trajectory() → {path}")

try:
    atif = result.to_atif()
    print(f"result.to_atif() → {len(atif['steps'])} steps")
except ImportError:
    print("result.to_atif() → Harbor not installed (expected)")

print("\n" + "=" * 60)
print("Tutorial 11 complete!")
print("=" * 60)
