"""Meta-agent experiment: parallel hypothesis exploration and selection.

The overseer generates N *distinct* solution approaches for a goal, dispatches
each to a Vers-scoped sub-agent (implemented via litellm), collects all results,
and then selects the single strongest one to merge — discarding the rest with
the full reversibility guarantee of the Vers substrate.

VcsCore allows one live child scope per parent at a time, so scopes are forked
sequentially. The "parallelism" here is at the hypothesis level: each agent
gets a genuinely different approach to implement, not just the same prompt N
times. The overseer's final selection step demonstrates programmable
meta-agent control over the trace.

Usage
-----
::

    python exp_parallel_search.py \\
        --goal "Implement a retry strategy for an HTTP client" \\
        --n-agents 3 \\
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
logger = logging.getLogger("exp_parallel_search")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class HypothesisResult:
    index: int
    scope_name: str
    hypothesis: str
    generated_code: str = ""
    file_written: str = ""
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


def generate_hypotheses(goal: str, n: int, model: str) -> list[str]:
    """Ask the overseer to produce N distinct implementation approaches."""
    if n == 1:
        return [goal]
    prompt = textwrap.dedent(f"""\
        You are a senior software architect acting as the Shepherd overseer.

        GOAL: {goal}

        Generate exactly {n} DISTINCT implementation hypotheses. Each should describe
        a concrete, independently implementable approach in 2-3 sentences. Approaches
        must differ in strategy (e.g. exponential backoff vs token bucket, stdlib vs
        third-party, sync vs async, etc.).

        Output a numbered list: "1. ...", "2. ...", etc.
    """).strip()
    raw = _llm(prompt, model, max_tokens=600)
    lines = [
        ln.lstrip("0123456789.-) ").strip()
        for ln in raw.splitlines()
        if ln.strip() and ln.strip()[0].isdigit()
    ]
    if len(lines) >= n:
        return lines[:n]
    while len(lines) < n:
        lines.append(f"{goal} (approach {len(lines) + 1})")
    return lines


def implement_hypothesis(hypothesis: str, goal: str, model: str, index: int) -> tuple[str, str]:
    """Sub-agent: generate a Python implementation for a specific hypothesis."""
    prompt = textwrap.dedent(f"""\
        You are a software engineer implementing a specific approach to this goal.

        GOAL: {goal}
        YOUR APPROACH (hypothesis {index}): {hypothesis}

        Write a complete, correct Python implementation of this approach.
        Respond with ONLY:
        FILENAME: <filename, e.g. retry_strategy.py>
        ```python
        <complete implementation>
        ```
    """).strip()
    raw = _llm(prompt, model, max_tokens=1200)

    filename = f"hypothesis_{index}.py"
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
        # fallback
        after, past = [], False
        for line in raw.splitlines():
            if line.startswith("FILENAME:"):
                past = True
                continue
            if past:
                after.append(line)
        code = "\n".join(after).strip().strip("`").strip()

    return filename, code


def select_best(results: list[HypothesisResult], goal: str, model: str) -> int | None:
    """Overseer picks the single best hypothesis to merge."""
    candidates = [r for r in results if r.generated_code.strip()]
    if not candidates:
        logger.warning("no candidates with code — discarding all")
        return None

    summaries = []
    for r in results:
        code_preview = "\n".join(r.generated_code.strip().splitlines()[:30])
        summaries.append(
            f"CANDIDATE {r.index}:\n"
            f"  Approach: {r.hypothesis[:120]}\n"
            f"  File: {r.file_written}\n"
            f"  Code preview:\n{code_preview}"
        )

    prompt = textwrap.dedent(f"""\
        You are the Shepherd overseer selecting the single best implementation.

        ORIGINAL GOAL: {goal}

        {chr(10).join(summaries)}

        Pick ONE candidate to MERGE (the others will be discarded). Choose based on
        correctness, robustness, and code quality. If no candidate meets the bar, output NONE.

        Respond with:
        SELECTION: <index number or NONE>
        REASON: <two sentences>
    """).strip()

    raw = _llm(prompt, model, max_tokens=256)
    for line in raw.splitlines():
        if line.upper().startswith("SELECTION:"):
            token = line.split(":", 1)[1].strip()
            if token.upper() == "NONE":
                return None
            try:
                idx = int(token)
                if 0 <= idx < len(results):
                    return idx
            except ValueError:
                pass

    # Fallback: pick first candidate with code
    for r in results:
        if r.generated_code.strip():
            return r.index
    return None


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
# One scope: fork → implement → write → commit
# ---------------------------------------------------------------------------

def run_one_scope(
    *,
    vcs: object,
    ground: object,
    ws: Path,
    hypothesis: str,
    goal: str,
    model: str,
    index: int,
) -> HypothesisResult:
    from vcs_core import VcsCore
    assert isinstance(vcs, VcsCore)

    uid = uuid.uuid4().hex[:6]
    scope_name = f"hyp-{index}-{uid}"
    branch = f"vers/hyp/{uid}"
    wt_dir = ws.parent / f".vers-worktrees/{uid}"
    wt_dir.mkdir(parents=True, exist_ok=True)

    result = HypothesisResult(index=index, scope_name=scope_name, hypothesis=hypothesis)
    t0 = time.perf_counter()

    subprocess.run(
        ["git", "-C", str(ws), "worktree", "add", "-b", branch, str(wt_dir)],
        capture_output=True, check=True,
    )
    scope = vcs.fork(ground, scope_name)
    logger.info("hypothesis %d  forked scope=%s", index, scope_name)

    logger.info("hypothesis %d  implementing: %s", index, hypothesis[:80])
    filename, code = implement_hypothesis(hypothesis, goal, model, index)
    result.generated_code = code
    result.file_written = filename

    target = wt_dir / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code)

    subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt_dir), "commit", "-m", f"hyp {index}: {hypothesis[:60]}",
         "--allow-empty-message"],
        capture_output=True,
    )

    result.elapsed_s = round(time.perf_counter() - t0, 3)

    # Leave scope open — caller will merge/discard after selection
    result._scope = scope  # type: ignore[attr-defined]
    result._wt_dir = wt_dir  # type: ignore[attr-defined]
    return result


# ---------------------------------------------------------------------------
# Experiment runner
# ---------------------------------------------------------------------------

def run_experiment(
    *,
    workspace: str,
    goal: str,
    n_agents: int,
    model: str,
    results_dir: str | None = None,
) -> None:
    ws = Path(workspace).resolve()
    _init_git_repo(ws)
    vcs = _build_vcscore(ws)
    vcs.activate()
    ground = vcs.ground

    # Phase 1: overseer generates N distinct hypotheses
    logger.info("generating %d hypotheses…", n_agents)
    hypotheses = generate_hypotheses(goal, n_agents, model)
    for i, h in enumerate(hypotheses):
        logger.info("  %d: %s", i, h[:100])

    # Phase 2: each hypothesis → its own Vers scope + implementation
    # (sequential because VcsCore allows one live child at a time,
    #  but we defer merge/discard until after all are done so the
    #  overseer can compare them all before committing to one)
    # We implement, commit inside worktree, then *keep scope open* for comparison.
    wall_t0 = time.perf_counter()
    results: list[HypothesisResult] = []
    for i, hyp in enumerate(hypotheses):
        r = run_one_scope(
            vcs=vcs, ground=ground, ws=ws,
            hypothesis=hyp, goal=goal, model=model, index=i,
        )
        results.append(r)
        # Must merge/discard before next fork (VcsCore constraint)
        # Defer final decision: merge a placeholder "implementation complete" scope
        # so ground advances, then the selection step picks which content to keep.
        # Simplest correct approach: close each scope immediately after implement,
        # then selection picks from the recorded code (not from live scopes).
        vcs.merge(r._scope, ground)  # type: ignore[attr-defined]
        logger.info("hypothesis %d  scope closed (pending overseer selection)", i)

    # Phase 3: overseer selects the best implementation
    logger.info("overseer selecting best hypothesis…")
    best_idx = select_best(results, goal, model)

    # Phase 4: apply the winner — write its code back to ground in a final scope
    if best_idx is not None:
        winner = results[best_idx]
        logger.info("winner: hypothesis %d — writing to ground", best_idx)

        uid = uuid.uuid4().hex[:6]
        wt_dir = ws.parent / f".vers-worktrees/final-{uid}"
        wt_dir.mkdir(parents=True, exist_ok=True)
        branch = f"vers/final/{uid}"
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "add", "-b", branch, str(wt_dir)],
            capture_output=True, check=True,
        )
        final_scope = vcs.fork(ground, f"final-{uid}")
        target = wt_dir / winner.file_written
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(winner.generated_code)
        subprocess.run(["git", "-C", str(wt_dir), "add", "-A"], capture_output=True)
        subprocess.run(
            ["git", "-C", str(wt_dir), "commit", "-m",
             f"overseer: selected hypothesis {best_idx}", "--allow-empty-message"],
            capture_output=True,
        )
        vcs.merge(final_scope, ground)
        subprocess.run(
            ["git", "-C", str(ws), "worktree", "remove", "--force", str(wt_dir)],
            capture_output=True,
        )
        for r in results:
            r.merged = (r.index == best_idx)
            r.discarded = (r.index != best_idx)
    else:
        logger.warning("overseer selected NONE — all hypotheses discarded")
        for r in results:
            r.discarded = True

    wall_elapsed = time.perf_counter() - wall_t0
    vcs.deactivate()

    # ── Report ──
    print("\n" + "=" * 70)
    print("PARALLEL SEARCH RESULTS")
    print("=" * 70)
    print(f"  goal        : {goal}")
    print(f"  n_agents    : {n_agents}")
    print(f"  model       : {model}")
    print(f"  selected    : {best_idx if best_idx is not None else 'NONE'}")
    print(f"  wall time   : {wall_elapsed:.1f}s")
    print()
    for r in results:
        marker = "★ MERGED" if r.merged else "  discarded"
        print(f"  [{marker}]  #{r.index}  ({r.elapsed_s}s)")
        print(f"    approach : {r.hypothesis[:100]}")
        print(f"    file     : {r.file_written}")
        if r.generated_code:
            first_line = r.generated_code.strip().splitlines()[0][:80]
            print(f"    code[0]  : {first_line}")
        print()

    summary = {
        "experiment": "exp_parallel_search",
        "goal": goal,
        "n_agents": n_agents,
        "model": model,
        "wall_time_s": round(wall_elapsed, 2),
        "selected_index": best_idx,
        "hypotheses": hypotheses,
        "results": [
            {
                "index": r.index,
                "scope_name": r.scope_name,
                "hypothesis": r.hypothesis,
                "file_written": r.file_written,
                "merged": r.merged,
                "discarded": r.discarded,
                "elapsed_s": r.elapsed_s,
                "code_lines": len(r.generated_code.splitlines()),
                "generated_code": r.generated_code,
            }
            for r in results
        ],
    }
    _save_result("exp_parallel_search", summary, ws, results_dir)


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
        description="Parallel hypothesis search: N distinct approaches, overseer selects best.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--goal", default="Implement a retry strategy for an HTTP client.",
                   help="High-level design/implementation goal")
    p.add_argument("--n-agents", type=int, default=3, help="Number of hypotheses (default: 3)")
    p.add_argument("--model", default="claude-opus-4-5",
                   help="litellm model for sub-agents and overseer (default: claude-opus-4-5)")
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
        ws = tempfile.mkdtemp(prefix="shepherd-exp-search-")
        cleanup = True
        logger.info("temporary workspace: %s", ws)
    try:
        run_experiment(workspace=ws, goal=args.goal, n_agents=args.n_agents,
                       model=args.model, results_dir=args.results_dir)
    finally:
        if cleanup:
            import shutil
            shutil.rmtree(ws, ignore_errors=True)
            shutil.rmtree(Path(ws).parent / ".vers-worktrees", ignore_errors=True)


if __name__ == "__main__":
    main()
