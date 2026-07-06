"""Meta-agent experiment: N sub-agents tackle a coding task via Vers-isolated scopes.

Each sub-agent is a litellm call that generates code, which is written into an
isolated git worktree registered as a VcsCore scope. The local overseer then
evaluates each diff and merges the best result (or discards if none meet the bar).

VcsCore allows only one live child scope per parent at a time, so agents run
sequentially (fork → generate → write → commit → evaluate → merge/discard).
This still demonstrates the paper's core reversibility and programmable
meta-agent claims: the overseer is a real LLM making real merge/discard
decisions, and the trace records every outcome durably.

Usage
-----
::

    python exp_coding_task.py \\
        --task "Add a fibonacci function with memoization to utils.py" \\
        --n-agents 3 \\
        --model claude-opus-4-5

    # Point at an existing repo:
    python exp_coding_task.py \\
        --task "Add input validation to register() in auth.py" \\
        --workspace /path/to/my/repo \\
        --n-agents 2
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
logger = logging.getLogger("exp_coding_task")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    agent_index: int
    scope_name: str
    instruction: str
    generated_code: str = ""
    file_written: str = ""
    diff_text: str = ""
    merged: bool = False
    discarded: bool = False
    evaluation: str = ""
    accept: bool = False
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# LLM helpers (litellm with Anthropic)
# ---------------------------------------------------------------------------

def _llm(prompt: str, model: str, max_tokens: int = 1024) -> str:
    import litellm  # type: ignore[import-untyped]
    resp = litellm.completion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def generate_code(task: str, model: str, agent_index: int) -> tuple[str, str]:
    """Ask the model to write code for the task. Returns (filename, code)."""
    prompt = textwrap.dedent(f"""\
        You are a software engineer (agent {agent_index}) implementing the following task.
        Write clean, correct Python code.

        TASK: {task}

        Respond with ONLY:
        FILENAME: <relative filename, e.g. utils.py>
        ```python
        <your complete implementation>
        ```

        No explanations outside the code block.
    """).strip()

    raw = _llm(prompt, model, max_tokens=1024)

    # Parse filename
    filename = "output.py"
    for line in raw.splitlines():
        if line.startswith("FILENAME:"):
            filename = line.split(":", 1)[1].strip()
            break

    # Extract code block
    code = ""
    in_block = False
    lines = []
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
        # Fallback: take everything after FILENAME line
        after = []
        past_filename = False
        for line in raw.splitlines():
            if line.startswith("FILENAME:"):
                past_filename = True
                continue
            if past_filename:
                after.append(line)
        code = "\n".join(after).strip().strip("`").strip()

    return filename, code


def evaluate_result(result: AgentResult, task: str, model: str) -> tuple[str, bool]:
    """Ask the overseer to evaluate a diff and decide merge vs. discard."""
    prompt = textwrap.dedent(f"""\
        You are the Shepherd overseer reviewing a sub-agent's implementation.

        ORIGINAL TASK: {task}

        AGENT: {result.agent_index}
        FILE WRITTEN: {result.file_written}

        CODE:
        ```python
        {result.generated_code[:3000]}
        ```

        Evaluate whether this implementation should be MERGED to the main branch
        or DISCARDED. Criteria: correctness, completeness, code quality.

        Respond with:
        DECISION: MERGE  or  DECISION: DISCARD
        REASON: <one or two sentences>
    """).strip()

    reasoning = _llm(prompt, model, max_tokens=256)
    accept = "DECISION: MERGE" in reasoning.upper()
    return reasoning, accept


# ---------------------------------------------------------------------------
# Workspace + VcsCore setup
# ---------------------------------------------------------------------------

def _init_git_repo(ws: Path) -> None:
    """Ensure ws is a git repo with at least one commit."""
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
# One agent run: fork → generate → write → commit → evaluate → merge/discard
# ---------------------------------------------------------------------------

def run_one_agent(
    *,
    vcs: object,
    ground: object,
    ws: Path,
    task: str,
    model: str,
    agent_index: int,
) -> AgentResult:
    from vcs_core import VcsCore

    assert isinstance(vcs, VcsCore)

    uid = uuid.uuid4().hex[:8]
    scope_name = f"agent-{agent_index}-{uid[:6]}"
    branch_name = f"vers/agent/{uid[:6]}"
    wt_dir = ws.parent / f".vers-worktrees/{uid[:6]}"
    wt_dir.mkdir(parents=True, exist_ok=True)

    result = AgentResult(
        agent_index=agent_index,
        scope_name=scope_name,
        instruction=task,
    )

    t0 = time.perf_counter()

    # 1. Fork a Vers scope from ground
    subprocess.run(
        ["git", "-C", str(ws), "worktree", "add", "-b", branch_name, str(wt_dir)],
        capture_output=True, check=True,
    )
    scope = vcs.fork(ground, scope_name)
    logger.info("agent %d  forked scope=%s  worktree=%s", agent_index, scope_name, wt_dir)

    # 2. Sub-agent: LLM generates code
    logger.info("agent %d  generating code…", agent_index)
    filename, code = generate_code(task, model, agent_index)
    result.generated_code = code
    result.file_written = filename

    # 3. Write the generated code into the worktree
    target = wt_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code)

    # Stage + commit inside the worktree
    subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt_dir), "commit", "-m", f"agent {agent_index}: {task[:60]}",
         "--allow-empty-message"],
        capture_output=True,
    )

    # Capture diff for the overseer
    diff_proc = subprocess.run(
        ["git", "-C", str(ws), "diff", "HEAD", branch_name],
        capture_output=True, text=True,
    )
    result.diff_text = diff_proc.stdout

    # 4. Overseer evaluates the result
    logger.info("agent %d  evaluating…", agent_index)
    evaluation, accept = evaluate_result(result, task, model)
    result.evaluation = evaluation
    result.accept = accept

    # 5. Merge or discard
    if accept and code.strip():
        logger.info("agent %d  ACCEPTED — merging scope=%s", agent_index, scope_name)
        vcs.merge(scope, ground)
        result.merged = True
    else:
        reason = "overseer rejected" if not accept else "agent produced no code"
        logger.info("agent %d  REJECTED (%s) — discarding scope=%s", agent_index, reason, scope_name)
        vcs.discard(scope)
        result.discarded = True

    # Clean up worktree
    subprocess.run(
        ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
        capture_output=True,
    )

    result.elapsed_s = round(time.perf_counter() - t0, 3)
    return result


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    *,
    workspace: str,
    task: str,
    n_agents: int,
    model: str,
    results_dir: str | None = None,
) -> list[AgentResult]:
    ws = Path(workspace).resolve()
    _init_git_repo(ws)

    vcs = _build_vcscore(ws)
    vcs.activate()
    ground = vcs.ground

    logger.info("workspace=%s  task=%r  n_agents=%d  model=%s", ws, task[:60], n_agents, model)

    results: list[AgentResult] = []
    wall_t0 = time.perf_counter()

    # VcsCore allows only one live child scope per parent → run sequentially
    for i in range(n_agents):
        r = run_one_agent(
            vcs=vcs,
            ground=ground,
            ws=ws,
            task=task,
            model=model,
            agent_index=i,
        )
        results.append(r)

    wall_elapsed = time.perf_counter() - wall_t0
    vcs.deactivate()

    # ── Report ──
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    merged   = [r for r in results if r.merged]
    discarded = [r for r in results if r.discarded]
    print(f"  task         : {task}")
    print(f"  n_agents     : {n_agents}")
    print(f"  model        : {model}")
    print(f"  merged       : {len(merged)}")
    print(f"  discarded    : {len(discarded)}")
    print(f"  wall time    : {wall_elapsed:.1f}s")
    print()

    for r in results:
        status = "✓ MERGED" if r.merged else "✗ DISCARDED"
        print(f"  [{status}]  agent {r.agent_index}  scope={r.scope_name}  ({r.elapsed_s}s)")
        print(f"    file     : {r.file_written}")
        print(f"    decision : {r.evaluation.strip()[:200]}")
        if r.generated_code:
            preview = r.generated_code.strip().splitlines()
            print(f"    code     : {preview[0][:80]}" + (" …" if len(preview) > 1 else ""))
        print()

    summary = {
        "experiment": "exp_coding_task",
        "task": task,
        "n_agents": n_agents,
        "model": model,
        "wall_time_s": round(wall_elapsed, 2),
        "merged": len(merged),
        "discarded": len(discarded),
        "results": [
            {
                "agent_index": r.agent_index,
                "scope_name": r.scope_name,
                "file_written": r.file_written,
                "merged": r.merged,
                "discarded": r.discarded,
                "elapsed_s": r.elapsed_s,
                "evaluation": r.evaluation[:400],
                "code_lines": len(r.generated_code.splitlines()),
                "generated_code": r.generated_code,
            }
            for r in results
        ],
    }
    _save_result("exp_coding_task", summary, ws, results_dir)
    return results



def _save_result(name: str, data: dict, ws: Path, results_dir: str | None) -> None:
    payload = json.dumps(data, indent=2)
    ws_path = ws / f"{name}.json"
    ws_path.write_text(payload)
    print(f"Result  → {ws_path}")
    if results_dir:
        import datetime
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
        description="Meta-agent coding experiment: N sequential Vers-scoped LLM sub-agents.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--task",
        default="Add a fibonacci function with memoization to utils.py",
        help="Coding task for every sub-agent",
    )
    p.add_argument("--n-agents", type=int, default=3,
                   help="Number of sub-agents (default: 3)")
    p.add_argument("--model", default="claude-opus-4-5",
                   help="litellm model for both sub-agents and overseer (default: claude-opus-4-5)")
    p.add_argument("--workspace", default=None,
                   help="Path to a git repo (default: fresh tmpdir)")
    p.add_argument("--results-dir", default=None,
                   help="Directory to persist JSON results for later review")
    return p


def main() -> None:
    args = _parser().parse_args()

    if args.workspace:
        ws = args.workspace
        cleanup = False
    else:
        ws = tempfile.mkdtemp(prefix="shepherd-exp-coding-")
        cleanup = True
        logger.info("temporary workspace: %s", ws)

    try:
        run_experiment(
            workspace=ws,
            task=args.task,
            n_agents=args.n_agents,
            model=args.model,
            results_dir=args.results_dir,
        )
    finally:
        if cleanup:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(Path(ws).parent / ".vers-worktrees", ignore_errors=True)


if __name__ == "__main__":
    main()
