"""Meta-agent experiment: multi-step refactor with per-step scope isolation.

Each step in the pipeline is a Vers scope forked from the *current ground*.
The sub-agent (litellm) implements the step, the overseer evaluates the diff,
and the scope is either merged (advancing ground) or discarded (halting the
pipeline cleanly with no partial state leak).

Pipeline shape
--------------
::

    ground ──fork──► scope-step-0  ──merge──► ground'
    ground' ─fork──► scope-step-1  ──merge──► ground''
    ground'' ─fork──► scope-step-2  ──merge──► ground'''
    ...

If step K is rejected, ground stays at ground^(K-1) and the pipeline halts.
Earlier accepted work is preserved exactly — no rollback needed.

Usage
-----
::

    python exp_multi_step_refactor.py \\
        --model claude-opus-4-5

    # Custom steps:
    python exp_multi_step_refactor.py \\
        --steps "Add type hints to all functions in utils.py" \\
                "Add docstrings to all public functions in utils.py" \\
                "Add an __all__ list to utils.py" \\
        --model claude-opus-4-5
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
import textwrap
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("exp_multi_step_refactor")

_DEFAULT_STEPS = [
    "Create utils.py with a fibonacci function (iterative, no memoization) and a factorial function.",
    "Refactor utils.py: add type hints to both functions.",
    "Refactor utils.py: add a docstring to each function explaining its parameters and return value.",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    step_index: int
    instruction: str
    scope_name: str
    file_written: str = ""
    generated_code: str = ""
    evaluation: str = ""
    accept: bool = False
    merged: bool = False
    discarded: bool = False
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _llm(prompt: str, model: str, max_tokens: int = 1024) -> str:
    import litellm  # type: ignore[import-untyped]
    resp = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def implement_step(instruction: str, existing_code: str, model: str, step_index: int) -> tuple[str, str]:
    """Sub-agent: generate the updated file content for this step."""
    context = (
        f"EXISTING FILE CONTENT:\n```python\n{existing_code}\n```\n\n"
        if existing_code.strip()
        else "No existing file — create it from scratch.\n\n"
    )
    prompt = textwrap.dedent(f"""\
        You are a software engineer implementing step {step_index} of a refactor pipeline.

        {context}TASK FOR THIS STEP: {instruction}

        Write the complete updated file. Respond with ONLY:
        FILENAME: <filename, e.g. utils.py>
        ```python
        <complete file content>
        ```
    """).strip()

    raw = _llm(prompt, model, max_tokens=1200)

    filename = "utils.py"
    for line in raw.splitlines():
        if line.startswith("FILENAME:"):
            filename = line.split(":", 1)[1].strip()
            break

    lines, in_block = [], False
    for line in raw.splitlines():
        if line.startswith("```python"):
            in_block = True
            continue
        if in_block and line.startswith("```"):
            break
        if in_block:
            lines.append(line)
    code = "\n".join(lines).strip()
    if not code:
        after, past = [], False
        for line in raw.splitlines():
            if line.startswith("FILENAME:"):
                past = True
                continue
            if past:
                after.append(line)
        code = "\n".join(after).strip().strip("`").strip()

    return filename, code


def evaluate_step(instruction: str, before: str, after: str, model: str, step_index: int) -> tuple[str, bool]:
    """Overseer evaluates the diff between before and after this step."""
    prompt = textwrap.dedent(f"""\
        You are the Shepherd overseer reviewing step {step_index} of a refactor pipeline.

        STEP INSTRUCTION: {instruction}

        BEFORE:
        ```python
        {before[:1500] if before else "(file did not exist)"}
        ```

        AFTER:
        ```python
        {after[:1500]}
        ```

        Does the change correctly and completely fulfil the step's instruction?
        Is the code still correct after the change?

        Respond with:
        DECISION: MERGE  or  DECISION: DISCARD
        REASON: <one or two sentences>
    """).strip()

    reasoning = _llm(prompt, model, max_tokens=256)
    accept = "DECISION: MERGE" in reasoning.upper()
    return reasoning, accept


# ---------------------------------------------------------------------------
# Workspace setup
# ---------------------------------------------------------------------------

def _init_git_repo(ws: Path) -> None:
    if (ws / ".git").exists():
        return
    subprocess.run(["git", "init", str(ws)], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "shepherd@exp"], capture_output=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "Shepherd Exp"], capture_output=True)
    (ws / "README.md").write_text("# Shepherd experiment workspace\n")
    subprocess.run(["git", "-C", str(ws), "add", "."], capture_output=True, check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-m", "init"], capture_output=True, check=True)


def _build_vcscore(ws: Path) -> object:
    from vcs_core import (
        DeclarativeFilesystemSubstrate,
        Store,
        VcsCore,
        build_builtin_substrate_context,
    )
    store = Store(str(ws / ".vcscore"))
    if store.is_empty:
        store.create_root_commit()
    ctx = build_builtin_substrate_context(store, workspace=ws)
    fs = DeclarativeFilesystemSubstrate(ctx)
    return VcsCore(str(ws), substrates=[fs], store=store)


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    *,
    workspace: str,
    steps: list[str],
    model: str,
    results_dir: str | None = None,
) -> None:
    ws = Path(workspace).resolve()
    _init_git_repo(ws)
    vcs = _build_vcscore(ws)
    vcs.activate()
    ground = vcs.ground

    logger.info("pipeline  steps=%d  model=%s", len(steps), model)
    step_results: list[StepResult] = []
    wall_t0 = time.perf_counter()

    # Carry the current committed file state forward between steps.
    # VcsCore manages its own store and doesn't sync back to the physical
    # git HEAD that `worktree add` branches from, so we track state in memory.
    current_files: dict[str, str] = {}  # filename → contents after last merge

    for step_idx, instruction in enumerate(steps):
        logger.info("─── step %d/%d: %s", step_idx + 1, len(steps), instruction[:70])
        t0 = time.perf_counter()

        uid = uuid.uuid4().hex[:6]
        scope_name = f"step-{step_idx}-{uid}"
        branch = f"vers/step/{step_idx}/{uid}"
        wt_dir = ws.parent / f".vers-worktrees/step{step_idx}-{uid}"
        wt_dir.mkdir(parents=True, exist_ok=True)

        # Fork a scope from the current ground
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "add", "-b", branch, str(wt_dir)],
            capture_output=True, check=True,
        )
        scope = vcs.fork(ground, scope_name)
        logger.info("step %d  forked scope=%s", step_idx, scope_name)

        # Seed the worktree with all files committed in previous steps
        for fname, fcode in current_files.items():
            target = wt_dir / fname
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(fcode)

        sr = StepResult(step_index=step_idx, instruction=instruction, scope_name=scope_name)

        # Derive existing_code context: the primary Python file (most lines), if any
        existing_code = ""
        if current_files:
            primary = max(current_files.items(), key=lambda kv: len(kv[1].splitlines()))
            existing_code = primary[1]
            logger.info("step %d  carrying forward: %s (%d lines)", step_idx, primary[0], len(existing_code.splitlines()))

        # Sub-agent implements the step
        logger.info("step %d  generating implementation…", step_idx)
        filename, code = implement_step(instruction, existing_code, model, step_idx)
        sr.file_written = filename
        sr.generated_code = code

        target = wt_dir / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        before_code = target.read_text() if target.exists() else ""
        target.write_text(code)

        subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True)
        subprocess.run(
            ["git", "-C", str(wt_dir), "commit",
             "-m", f"step {step_idx}: {instruction[:60]}", "--allow-empty-message"],
            capture_output=True,
        )

        # Overseer evaluates the diff
        logger.info("step %d  overseer evaluating…", step_idx)
        evaluation, accept = evaluate_step(instruction, before_code, code, model, step_idx)
        sr.evaluation = evaluation
        sr.accept = accept
        sr.elapsed_s = round(time.perf_counter() - t0, 3)

        if accept and code.strip():
            logger.info("step %d  ACCEPTED — merging", step_idx)
            vcs.merge(scope, ground)
            sr.merged = True
            current_files[filename] = code  # carry forward for next step
        else:
            reason = "overseer rejected" if not accept else "agent produced no code"
            logger.info("step %d  REJECTED (%s) — discarding, halting pipeline", step_idx, reason)
            vcs.discard(scope)
            sr.discarded = True
            step_results.append(sr)
            subprocess.run(
                ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
                capture_output=True,
            )
            logger.info("pipeline halted at step %d; ground holds steps 0..%d", step_idx, step_idx - 1)
            break

        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
        )
        step_results.append(sr)

    wall_elapsed = time.perf_counter() - wall_t0
    vcs.deactivate()

    completed = sum(1 for r in step_results if r.merged)

    # ── Report ──
    print("\n" + "=" * 70)
    print("MULTI-STEP PIPELINE RESULTS")
    print("=" * 70)
    print(f"  steps total  : {len(steps)}")
    print(f"  completed    : {completed}")
    print(f"  model        : {model}")
    print(f"  wall time    : {wall_elapsed:.1f}s")
    print()
    for r in step_results:
        status = "✓ MERGED" if r.merged else "✗ DISCARDED"
        print(f"  [{status}]  step {r.step_index}  scope={r.scope_name}  ({r.elapsed_s}s)")
        print(f"    instruction : {r.instruction[:80]}")
        print(f"    file        : {r.file_written}")
        print(f"    decision    : {r.evaluation.strip()[:200]}")
        if r.generated_code:
            first = r.generated_code.strip().splitlines()[0][:80]
            print(f"    code[0]     : {first}")
        print()

    summary = {
        "experiment": "exp_multi_step_refactor",
        "steps": steps,
        "model": model,
        "total_elapsed_s": round(wall_elapsed, 2),
        "completed": completed,
        "halted": completed < len(steps),
        "step_results": [
            {
                "step_index": r.step_index,
                "instruction": r.instruction,
                "scope_name": r.scope_name,
                "file_written": r.file_written,
                "merged": r.merged,
                "discarded": r.discarded,
                "elapsed_s": r.elapsed_s,
                "code_lines": len(r.generated_code.splitlines()),
                "evaluation": r.evaluation,
                "generated_code": r.generated_code,
            }
            for r in step_results
        ],
    }
    _save_result("exp_multi_step_refactor", summary, ws, results_dir)


def _save_result(name: str, data: dict, ws: Path, results_dir: str | None) -> None:
    import datetime
    payload = json.dumps(data, indent=2)
    ws_path = ws / f"{name}.json"
    ws_path.write_text(payload)
    print(f"Result  → {ws_path}")
    if results_dir:
        rd = Path(results_dir)
        rd.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        out = rd / f"{name}_{ts}.json"
        out.write_text(payload)
        print(f"Persisted → {out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Multi-step refactor: each step gets an isolated Vers scope.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--steps", nargs="+", default=_DEFAULT_STEPS,
                   help="Ordered step instructions")
    p.add_argument("--model", default="claude-opus-4-5")
    p.add_argument("--workspace", default=None,
                   help="Path to a git repo (default: fresh tmpdir)")
    p.add_argument("--results-dir", default=None,
                   help="Directory to persist JSON results for later review")
    return p


def main() -> None:
    args = _parser().parse_args()
    if args.workspace:
        ws, cleanup = args.workspace, False
    else:
        ws = tempfile.mkdtemp(prefix="shepherd-exp-pipeline-")
        cleanup = True
        logger.info("temporary workspace: %s", ws)
    try:
        run_experiment(workspace=ws, steps=args.steps, model=args.model,
                       results_dir=args.results_dir)
    finally:
        if cleanup:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(Path(ws).parent / ".vers-worktrees", ignore_errors=True)


if __name__ == "__main__":
    main()
