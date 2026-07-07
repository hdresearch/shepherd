"""Local overseer: Anthropic SDK calls that run on the host (outside the VM jail).

The overseer never touches the Shepherd substrate itself — it reads retained
outputs via the changeset API (read-only, no jail required) and then calls
``output.select()`` or ``output.discard()`` to settle them.

All LLM calls go directly through the Anthropic Python SDK using the ambient
``ANTHROPIC_API_KEY``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import anthropic

_DEFAULT_MODEL = "claude-opus-4-5"


def _client() -> anthropic.Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


def overseer_call(prompt: str, *, model: str = _DEFAULT_MODEL) -> str:
    """Single-shot overseer call; returns the text response."""
    client = _client()
    resp = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


@dataclass
class EvaluatedRun:
    run: Any          # WorkspaceRun
    score: float      # 0-10
    rationale: str
    changed_paths: tuple[str, ...]
    code_preview: str  # first 800 chars of the primary output file


def evaluate_runs(
    runs: list[Any],
    task_description: str,
    primary_output: str = "solution.py",
    *,
    model: str = _DEFAULT_MODEL,
) -> list[EvaluatedRun]:
    """Ask the local overseer to score each run's retained output.

    Reads file content via ``changeset.read_file(path)`` (no jail, no select).
    """
    evaluated: list[EvaluatedRun] = []
    for run in runs:
        cs = run.changeset()
        changed = cs.changed_paths
        # Try to read the primary output, fall back to first changed file.
        raw: bytes | None = None
        chosen_path = primary_output
        raw_result = cs.read_file(primary_output)
        if raw_result is not None:
            raw, _ = raw_result
        elif changed:
            chosen_path = changed[0]
            raw_result2 = cs.read_file(chosen_path)
            if raw_result2 is not None:
                raw, _ = raw_result2

        full_content = raw.decode("utf-8", errors="replace") if raw else ""
        preview = full_content[:4000] if full_content else "(no output)"
        truncated = len(full_content) > 4000

        prompt = (
            f"You are evaluating a coding agent's output for the following task:\n\n"
            f"TASK: {task_description}\n\n"
            f"OUTPUT FILE: {chosen_path}\n"
            f"CONTENT ({len(full_content)} bytes{', truncated' if truncated else ''}):\n```\n{preview}\n```\n\n"
            f"Changed paths: {list(changed)}\n\n"
            f"Score this output from 0 to 10 (10 = perfect). "
            f"Reply with exactly: SCORE: <number>\nRATIONALE: <one sentence>"
        )
        reply = overseer_call(prompt, model=model)
        score = _parse_score(reply)
        rationale = _parse_rationale(reply)
        evaluated.append(EvaluatedRun(
            run=run,
            score=score,
            rationale=rationale,
            changed_paths=tuple(changed),
            code_preview=full_content[:800],
        ))
    return evaluated


def select_best(
    evaluated: list[EvaluatedRun],
    *,
    min_score: float = 5.0,
) -> EvaluatedRun | None:
    """Select the highest-scoring run above *min_score*; discard all others.

    Returns the selected ``EvaluatedRun`` (or None if none cleared the bar).
    """
    if not evaluated:
        return None

    ranked = sorted(evaluated, key=lambda e: e.score, reverse=True)
    winner = ranked[0] if ranked[0].score >= min_score else None

    for ev in evaluated:
        if winner is not None and ev is winner:
            ev.run.output().select()
        else:
            ev.run.output().discard()

    return winner


def generate_hypotheses(task: str, n: int, *, model: str = _DEFAULT_MODEL) -> list[str]:
    """Ask the overseer to produce *n* distinct implementation approaches for *task*."""
    prompt = (
        f"You are a software architect. For the task below, generate exactly {n} distinct "
        f"implementation approaches. Format them as:\n\n"
        f"1. <approach title>: <one paragraph description>\n"
        f"2. <approach title>: <one paragraph description>\n"
        f"...\n\n"
        f"Do NOT use markdown headers or bullet points. Just numbered paragraphs.\n\n"
        f"TASK: {task}"
    )
    reply = overseer_call(prompt, model=model)
    lines = [ln.strip() for ln in reply.splitlines() if ln.strip()]
    approaches: list[str] = []
    current = ""
    import re
    numbered_re = re.compile(r"^(\d+)[.\)]\s+")
    for ln in lines:
        m = numbered_re.match(ln)
        if m:
            if current:
                approaches.append(current.strip())
            current = ln[m.end():].strip()
        else:
            current = (current + " " + ln).strip() if current else ln
    if current:
        approaches.append(current.strip())
    # Pad or trim to exactly n.
    while len(approaches) < n:
        approaches.append(f"Standard approach #{len(approaches) + 1} for: {task}")
    return approaches[:n]


# ── helpers ─────────────────────────────────────────────────────────────────

def _parse_score(text: str) -> float:
    for line in text.splitlines():
        if line.strip().upper().startswith("SCORE:"):
            try:
                return float(line.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                pass
    # Fallback: look for first float in text.
    import re
    m = re.search(r"\b(\d+(?:\.\d+)?)\b", text)
    if m:
        val = float(m.group(1))
        return min(val, 10.0)
    return 5.0


def _parse_rationale(text: str) -> str:
    for line in text.splitlines():
        if line.strip().upper().startswith("RATIONALE:"):
            return line.split(":", 1)[1].strip()
    # Fall back to whole reply.
    return text[:200]
