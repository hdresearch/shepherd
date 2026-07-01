"""Tier 2: ATIF (Agent Trajectory Interchange Format) export/import.

Converts between the shepherd effect stream and Harbor's ATIF format.
Requires Harbor to be installed — Tier 1 (JSON) and Tier 3 (Trajectory) work without it.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shepherd_core.scope.stream import Stream

try:
    from harbor.models.trajectories import (  # type: ignore[import]
        Agent as ATIFAgent,
    )
    from harbor.models.trajectories import (  # type: ignore[import]
        Observation,
        ObservationResult,
        Step,
        Trajectory,
    )
    from harbor.models.trajectories import (  # type: ignore[import]
        ToolCall as ATIFToolCall,
    )

    _HAS_HARBOR = True
except ImportError:
    _HAS_HARBOR = False


def _require_harbor() -> None:
    if not _HAS_HARBOR:
        raise ImportError(
            "ATIF export/import requires the 'harbor' package. "
            "Install it with: pip install harbor  (or ensure harbor is on your PYTHONPATH)"
        )


def _effect_to_timestamp(effect: Any) -> str | None:
    """Extract ISO 8601 timestamp from an effect."""
    timestamp = getattr(effect, "timestamp", None)
    if timestamp is None:
        return None
    if isinstance(timestamp, (int, float)):
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()
    return str(timestamp)


def _group_effects_into_steps(stream: Stream) -> list[dict[str, Any]]:
    """Group contiguous effects into ATIF step descriptors."""
    steps: list[dict[str, Any]] = []
    pending_thinking: str | None = None
    pending_agent: dict[str, Any] | None = None

    def flush_agent() -> None:
        nonlocal pending_agent, pending_thinking
        if pending_agent is not None:
            if pending_thinking:
                pending_agent["reasoning_content"] = pending_thinking
                pending_thinking = None
            steps.append(pending_agent)
            pending_agent = None

    for layer in stream:
        effect = layer.effect
        effect_type = effect.effect_type

        if effect_type == "prompt_sent":
            flush_agent()
            steps.append(
                {
                    "source": "user",
                    "message": getattr(effect, "user_prompt", "") or getattr(effect, "system_prompt", ""),
                    "timestamp": _effect_to_timestamp(effect),
                }
            )

        elif effect_type == "agent_thinking":
            content = getattr(effect, "content", "")
            if pending_thinking:
                pending_thinking += "\n" + content
            else:
                pending_thinking = content

        elif effect_type == "agent_message":
            flush_agent()
            pending_agent = {
                "source": "agent",
                "message": getattr(effect, "content", ""),
                "timestamp": _effect_to_timestamp(effect),
                "model_name": getattr(effect, "model_name", None) or getattr(effect, "provider_id", None),
                "tool_calls": [],
                "observations": [],
            }
            if pending_thinking:
                pending_agent["reasoning_content"] = pending_thinking
                pending_thinking = None

        elif effect_type == "tool_call_started":
            if pending_agent is None:
                pending_agent = {
                    "source": "agent",
                    "message": "",
                    "timestamp": _effect_to_timestamp(effect),
                    "model_name": getattr(effect, "provider_id", None),
                    "tool_calls": [],
                    "observations": [],
                }
                if pending_thinking:
                    pending_agent["reasoning_content"] = pending_thinking
                    pending_thinking = None

            tool_call_id = getattr(effect, "tool_call_id", None) or str(uuid.uuid4())[:8]
            tool_name = getattr(effect, "tool_name", "unknown")
            arguments = getattr(effect, "params", {}) or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (json.JSONDecodeError, TypeError):
                    arguments = {"raw": arguments}

            pending_agent["tool_calls"].append(
                {
                    "tool_call_id": tool_call_id,
                    "function_name": tool_name,
                    "arguments": arguments,
                }
            )

        elif effect_type == "tool_call_completed":
            if pending_agent is not None:
                tool_call_id = getattr(effect, "tool_call_id", None)
                output = getattr(effect, "output", "") or getattr(effect, "result", "")
                if isinstance(output, dict):
                    output = json.dumps(output)
                pending_agent["observations"].append(
                    {
                        "source_call_id": tool_call_id,
                        "content": str(output),
                    }
                )

        elif effect_type in ("task_started", "task_completed", "task_failed"):
            flush_agent()
            task_name = getattr(effect, "task_name", "")
            if effect_type == "task_started":
                message = f"Task started: {task_name}"
            elif effect_type == "task_completed":
                message = f"Task completed: {task_name}"
            else:
                error = getattr(effect, "error", "unknown")
                message = f"Task failed: {task_name} — {error}"
            steps.append(
                {
                    "source": "system",
                    "message": message,
                    "timestamp": _effect_to_timestamp(effect),
                }
            )

    flush_agent()
    return steps


def to_atif(
    stream: Stream,
    *,
    session_id: str | None = None,
    agent_name: str = "shepherd",
    agent_version: str = "2.0",
    model_name: str | None = None,
) -> dict[str, Any]:
    """Export a Stream as an ATIF-compatible dictionary."""
    _require_harbor()

    if session_id is None:
        session_id = str(uuid.uuid4())

    step_descriptors = _group_effects_into_steps(stream)

    atif_steps: list[Step] = []
    for index, desc in enumerate(step_descriptors, start=1):
        step_kwargs: dict[str, Any] = {
            "step_id": index,
            "source": desc["source"],
            "message": desc.get("message", ""),
        }

        timestamp = desc.get("timestamp")
        if timestamp:
            step_kwargs["timestamp"] = timestamp

        if desc["source"] == "agent":
            if desc.get("model_name"):
                step_kwargs["model_name"] = desc["model_name"]
            if desc.get("reasoning_content"):
                step_kwargs["reasoning_content"] = desc["reasoning_content"]

            tool_calls = desc.get("tool_calls", [])
            if tool_calls:
                step_kwargs["tool_calls"] = [
                    ATIFToolCall(
                        tool_call_id=tool_call["tool_call_id"],
                        function_name=tool_call["function_name"],
                        arguments=tool_call["arguments"],
                    )
                    for tool_call in tool_calls
                ]

            observations = desc.get("observations", [])
            if observations:
                step_kwargs["observation"] = Observation(
                    results=[
                        ObservationResult(
                            source_call_id=observation.get("source_call_id"),
                            content=observation.get("content", ""),
                        )
                        for observation in observations
                    ]
                )

        atif_steps.append(Step(**step_kwargs))

    if not atif_steps:
        atif_steps.append(Step(step_id=1, source="system", message="Empty trajectory"))

    agent = ATIFAgent(
        name=agent_name,
        version=agent_version,
        model_name=model_name,
    )

    trajectory = Trajectory(
        session_id=session_id,
        agent=agent,
        steps=atif_steps,
    )

    result: dict[str, Any] = trajectory.to_json_dict(exclude_none=True)
    return result


def to_atif_json(
    stream: Stream,
    *,
    indent: int = 2,
    **kwargs: Any,
) -> str:
    """Export a Stream as an ATIF JSON string."""
    return json.dumps(to_atif(stream, **kwargs), indent=indent, default=str)


def from_atif(atif_dict: dict[str, Any]) -> Stream:
    """Import an ATIF trajectory dict as a Stream."""
    from shepherd_core.effects import (
        AgentMessage,
        AgentThinking,
        PromptSent,
        TaskCompleted,
        TaskStarted,
        ToolCallCompleted,
        ToolCallStarted,
    )

    _require_harbor()

    trajectory = Trajectory.model_validate(atif_dict)
    stream = Stream()

    for step in trajectory.steps:
        if isinstance(step.message, list):
            message_text = " ".join(part.text for part in step.message if hasattr(part, "text") and part.text)
        else:
            message_text = step.message or ""

        if step.source == "user":
            stream = stream.append(PromptSent(user_prompt=message_text))

        elif step.source == "agent":
            if step.reasoning_content:
                stream = stream.append(AgentThinking(content=step.reasoning_content))

            if message_text:
                stream = stream.append(AgentMessage(content=message_text))

            if step.tool_calls:
                observations_by_id: dict[str, str] = {}
                if step.observation:
                    for observation_result in step.observation.results:
                        if observation_result.source_call_id:
                            content = observation_result.content
                            if isinstance(content, list):
                                content = " ".join(part.text for part in content if hasattr(part, "text") and part.text)
                            observations_by_id[observation_result.source_call_id] = str(content or "")

                for tool_call in step.tool_calls:
                    stream = stream.append(
                        ToolCallStarted(
                            tool_call_id=tool_call.tool_call_id,
                            tool_name=tool_call.function_name,
                            params=tool_call.arguments,
                        )
                    )
                    output = observations_by_id.get(tool_call.tool_call_id, "")
                    stream = stream.append(
                        ToolCallCompleted(
                            tool_call_id=tool_call.tool_call_id,
                            tool_name=tool_call.function_name,
                            output=output,
                        )
                    )

        elif step.source == "system":
            message_lower = message_text.lower()
            if "started" in message_lower:
                task_name = message_text.replace("Task started: ", "").strip()
                stream = stream.append(TaskStarted(task_name=task_name))
            elif "completed" in message_lower:
                task_name = message_text.replace("Task completed: ", "").strip()
                stream = stream.append(TaskCompleted(task_name=task_name))
            elif "failed" in message_lower:
                task_name = message_text.split("—")[0].replace("Task failed: ", "").strip()
                error = message_text.split("—")[-1].strip() if "—" in message_text else "unknown"
                from shepherd_core.effects import TaskFailed

                stream = stream.append(TaskFailed(task_name=task_name, error=error))
            else:
                stream = stream.append(TaskStarted(task_name=message_text))

    return stream


def from_atif_json(json_str: str) -> Stream:
    """Import an ATIF JSON string as a Stream."""
    return from_atif(json.loads(json_str))


def from_claude_code_session(session_path: str | Path) -> Stream:
    """Import a Claude Code session JSONL as a Stream."""
    _require_harbor()

    import shutil
    import tempfile

    from harbor.agents.installed.claude_code import ClaudeCode  # type: ignore[import]

    session_path = Path(session_path)

    if session_path.is_file():
        tmp_dir = Path(tempfile.mkdtemp(prefix="claude_session_"))
        try:
            shutil.copy2(session_path, tmp_dir / session_path.name)
            agent = ClaudeCode(logs_dir=Path(tempfile.mkdtemp()), model_name="unknown")
            trajectory = agent._convert_events_to_trajectory(tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        agent = ClaudeCode(logs_dir=Path(tempfile.mkdtemp()), model_name="unknown")
        trajectory = agent._convert_events_to_trajectory(session_path)

    if trajectory is None:
        return Stream()
    return from_atif(trajectory.to_json_dict(exclude_none=True))


__all__ = [
    "from_atif",
    "from_atif_json",
    "from_claude_code_session",
    "to_atif",
    "to_atif_json",
]
