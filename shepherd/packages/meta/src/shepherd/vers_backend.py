"""Vers infrastructure backend for the local Shepherd overseer.

Wires the Shepherd agent framework to vcs-core (Vers VCS substrate) so that
the Shepherd overseer runs **locally** while sub-agents commit, branch, fork,
and merge entirely inside Vers infra.

Architecture
------------
::

    ┌─────────────────────────────────────────────────┐
    │  Local machine                                  │
    │                                                 │
    │  VersShepherd (overseer)                        │
    │  ├── local LLM  ←─ plans tasks, evaluates diffs│
    │  └── VcsCore    ←─ Vers infra (git substrate)  │
    │       ├── fork()   → sub-agent branch           │
    │       ├── merge()  → accepted work → ground     │
    │       └── discard()→ rejected work archived     │
    │                                                 │
    │  VersAgentScope (per sub-agent)                 │
    │  ├── git worktree  ← isolated CWD for agent    │
    │  └── _claude_agent_runner subprocess            │
    │       ← AGENT_INSTRUCTION env var               │
    └─────────────────────────────────────────────────┘

Each sub-agent is fully isolated via a git worktree (cross-platform,
no FUSE required). The worktree branch is registered as a VcsCore scope.
After the agent subprocess exits the overseer reads the diff, queries the
local LLM, and calls ``VcsCore.merge()`` or ``VcsCore.discard()``.

Usage
-----
::

    from shepherd.vers_backend import VersShepherd

    shepherd = VersShepherd(
        workspace="path/to/repo",
        model="ollama/mistral",          # any litellm-compatible model
        overseer_model="ollama/mistral", # local model for oversight decisions
    )

    results = shepherd.run(
        "Add a REST endpoint for user authentication",
        n_agents=3,
        parallel=True,
    )
    for r in results:
        print(f"scope={r.scope_name} merged={r.merged} paths={r.changed_paths}")

``VersShepherd`` can also be used as a context manager::

    with VersShepherd("path/to/repo") as shepherd:
        results = shepherd.run("Refactor the DB layer")
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import tempfile
import textwrap
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from typing_extensions import Self

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SubAgentResult:
    """Outcome of one sub-agent execution inside a Vers scope.

    Attributes:
        scope_name: VcsCore scope (branch) name.
        instruction: The task the agent was given.
        success: Whether the agent subprocess exited 0.
        stdout: Agent subprocess stdout.
        stderr: Agent subprocess stderr.
        exit_code: Raw process exit code.
        changed_paths: Relative paths modified by the agent.
        diff_text: ``git diff`` between agent branch and ground.
        merged: True after the Shepherd accepted and merged this scope.
        discarded: True after the Shepherd rejected and discarded.
        evaluation: Overseer LLM reasoning for accept/reject.
    """

    scope_name: str
    instruction: str
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    changed_paths: list[str] = field(default_factory=list)
    diff_text: str = ""
    merged: bool = False
    discarded: bool = False
    evaluation: str = ""


# ---------------------------------------------------------------------------
# VersAgentScope — isolated sub-agent execution
# ---------------------------------------------------------------------------


class VersAgentScope:
    """A VcsCore scope hosting one sub-agent execution.

    A git worktree is created for isolation (works on macOS + Linux without
    FUSE). The agent subprocess writes files directly in the worktree, and
    the changes are committed to the worktree branch, making them available
    to VcsCore for merge/discard.

    Do not instantiate directly — use ``VersShepherd.open_scope()``.
    """

    def __init__(
        self,
        *,
        vcs: Any,  # vcs_core.VcsCore
        ground: Any,  # vcs_core.ScopeInfo
        scope_name: str,
        workspace: Path,
        worktree_dir: Path,
        branch_name: str,
    ) -> None:
        self._vcs = vcs
        self._ground = ground
        self._scope_name = scope_name
        self._workspace = workspace
        self._worktree_dir = worktree_dir
        self._branch_name = branch_name
        self._scope: Any = None  # set after fork()
        self._result: SubAgentResult | None = None

    # ── Public ──

    @property
    def scope_name(self) -> str:
        return self._scope_name

    @property
    def worktree_dir(self) -> Path:
        return self._worktree_dir

    def run_agent(
        self,
        instruction: str,
        *,
        timeout: float | None = None,
        allowed_tools: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> SubAgentResult:
        """Run the claude agent runner in this scope's worktree.

        The agent subprocess is given ``AGENT_INSTRUCTION`` and runs with
        ``cwd = worktree_dir``. File changes are committed to the scope
        branch after completion.

        Args:
            instruction: What the agent should do.
            timeout: Optional subprocess timeout in seconds.
            allowed_tools: Restrict to a read-only subset (passed as
                ``AGENT_ALLOWED_TOOLS`` JSON list).
            extra_env: Additional environment variables for the subprocess.

        Returns:
            :class:`SubAgentResult` with stdout/stderr, changed paths, and diff.
        """
        import json

        env = {**os.environ, "AGENT_INSTRUCTION": instruction}
        if allowed_tools is not None:
            env["AGENT_ALLOWED_TOOLS"] = json.dumps(allowed_tools)
        if extra_env:
            env.update(extra_env)

        runner_module = "shepherd._claude_agent_runner"
        cmd = [sys.executable, "-m", runner_module]

        logger.info("scope=%s  launching agent subprocess", self._scope_name)
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self._worktree_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("scope=%s  agent timed out", self._scope_name)
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            result = SubAgentResult(
                scope_name=self._scope_name,
                instruction=instruction,
                success=False,
                stdout=stdout,
                stderr=stderr,
                exit_code=-1,
            )
            self._result = result
            return result

        success = proc.returncode == 0
        logger.info("scope=%s  agent exited %d", self._scope_name, proc.returncode)

        # Commit whatever the agent wrote
        changed, diff = self._commit_agent_work(instruction)

        result = SubAgentResult(
            scope_name=self._scope_name,
            instruction=instruction,
            success=success,
            stdout=proc.stdout,
            stderr=proc.stderr,
            exit_code=proc.returncode,
            changed_paths=changed,
            diff_text=diff,
        )
        self._result = result
        return result

    def get_diff(self) -> str:
        """Return git diff of agent branch vs ground (HEAD of workspace)."""
        try:
            proc = subprocess.run(
                ["git", "diff", "HEAD", self._branch_name],
                cwd=str(self._workspace),
                capture_output=True,
                text=True,
            )
            return proc.stdout
        except Exception as exc:  # noqa: BLE001
            logger.warning("scope=%s  diff failed: %s", self._scope_name, exc)
            return ""

    # ── VcsCore lifecycle ──

    def _fork(self) -> None:
        """Register scope with VcsCore (fork from ground)."""
        try:
            self._scope = self._vcs.fork(self._ground, self._scope_name)
            logger.debug("scope=%s  forked from ground", self._scope_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("scope=%s  VcsCore.fork failed: %s  (continuing)", self._scope_name, exc)
            self._scope = None

    def merge(self) -> bool:
        """Merge this scope's work back to ground via VcsCore.

        Returns True if merge succeeded.
        """
        if self._scope is None:
            logger.warning("scope=%s  no VcsCore scope to merge", self._scope_name)
            return False
        try:
            self._vcs.merge(self._scope, self._ground)
            logger.info("scope=%s  merged to ground", self._scope_name)
            if self._result:
                self._result.merged = True
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("scope=%s  merge failed: %s", self._scope_name, exc)
            return False

    def discard(self) -> bool:
        """Discard this scope via VcsCore (archives the branch).

        Returns True if discard succeeded.
        """
        if self._scope is None:
            # Best-effort cleanup: delete git worktree
            self._cleanup_worktree()
            return True
        try:
            self._vcs.discard(self._scope)
            logger.info("scope=%s  discarded", self._scope_name)
            if self._result:
                self._result.discarded = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("scope=%s  discard failed: %s  (cleaning up)", self._scope_name, exc)
        finally:
            self._cleanup_worktree()
        return True

    # ── Internal ──

    def _commit_agent_work(self, instruction: str) -> tuple[list[str], str]:
        """Stage and commit agent's changes in the worktree."""
        git = ["git", "-C", str(self._worktree_dir)]

        # Stage all changes
        subprocess.run([*git, "add", "-A"], capture_output=True)

        # Check what changed
        status_proc = subprocess.run(
            [*git, "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
        )
        changed = [p.strip() for p in status_proc.stdout.splitlines() if p.strip()]

        if not changed:
            logger.info("scope=%s  no file changes", self._scope_name)
            return [], ""

        # Get diff text before commit
        diff_proc = subprocess.run(
            [*git, "diff", "--cached"],
            capture_output=True,
            text=True,
        )
        diff_text = diff_proc.stdout

        # Commit
        msg = f"vers-shepherd[{self._scope_name}]: {instruction[:72]}"
        subprocess.run(
            [*git, "commit", "-m", msg, "--allow-empty-message"],
            capture_output=True,
        )

        logger.info("scope=%s  committed %d file(s)", self._scope_name, len(changed))
        return changed, diff_text

    def _cleanup_worktree(self) -> None:
        """Remove git worktree."""
        try:
            subprocess.run(
                ["git", "-C", str(self._workspace), "worktree", "remove",
                 "--force", str(self._worktree_dir)],
                capture_output=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("worktree cleanup: %s", exc)


# ---------------------------------------------------------------------------
# VersShepherd — local overseer
# ---------------------------------------------------------------------------


class VersShepherd:
    """Local Shepherd overseer that manages Vers-hosted sub-agents.

    The overseer runs entirely on your local machine. Sub-agents run inside
    isolated git worktrees that are registered as VcsCore scopes (branches).
    Merge/discard decisions are made by a local LLM and executed via
    ``VcsCore.merge()`` / ``VcsCore.discard()``.

    Args:
        workspace: Path to a git repository (or directory; git will be init'd).
        model: LiteLLM model string used by sub-agents (default: claude-sonnet-4-5).
        overseer_model: LiteLLM model for the local overseer's evaluation.
            Falls back to ``model`` if not set.
        agent_timeout: Max seconds per sub-agent run (None = no limit).

    Example::

        shepherd = VersShepherd("my-repo", overseer_model="ollama/mistral")
        results = shepherd.run("Add a health-check endpoint", n_agents=2)
    """

    def __init__(
        self,
        workspace: str,
        *,
        model: str = "claude-sonnet-4-5",
        overseer_model: str | None = None,
        agent_timeout: float | None = None,
    ) -> None:
        self._workspace = Path(workspace).resolve()
        self._model = model
        self._overseer_model = overseer_model or model
        self._agent_timeout = agent_timeout
        self._vcs: Any = None
        self._ground: Any = None
        self._active_scopes: list[VersAgentScope] = []

    # ── Lifecycle ──

    def activate(self) -> None:
        """Initialize VcsCore and activate the ground scope."""
        self._ensure_git_repo()
        self._vcs = self._build_vcscore()
        self._vcs.activate()
        self._ground = self._vcs.ground
        logger.info("VersShepherd activated  workspace=%s", self._workspace)

    def deactivate(self) -> None:
        """Deactivate VcsCore."""
        if self._vcs is not None:
            try:
                self._vcs.deactivate(warn_on_open_scopes=False)
            except Exception as exc:  # noqa: BLE001
                logger.debug("deactivate: %s", exc)

    def __enter__(self) -> Self:
        self.activate()
        return self

    def __exit__(self, *exc: object) -> None:
        self.deactivate()

    # ── Main entry point ──

    def run(
        self,
        task: str,
        *,
        n_agents: int = 1,
        parallel: bool = False,
        auto_merge: bool = True,
    ) -> list[SubAgentResult]:
        """Run the shepherd loop for a high-level task.

        1. Plans sub-tasks (one per agent) via the overseer LLM.
        2. Spawns ``n_agents`` sub-agents in isolated Vers scopes.
        3. Evaluates each result with the overseer LLM.
        4. Merges accepted work; discards the rest.

        Args:
            task: High-level goal description.
            n_agents: Number of sub-agents to launch.
            parallel: Run agents concurrently (True) or sequentially (False).
            auto_merge: If True, automatically merge accepted branches.
                Set False to review results before merging.

        Returns:
            List of :class:`SubAgentResult`, one per agent.
        """
        if self._vcs is None:
            self.activate()

        instructions = self._plan_subtasks(task, n_agents)
        scopes = [self._open_scope() for _ in instructions]

        if parallel:
            results = self._run_parallel(scopes, instructions)
        else:
            results = self._run_sequential(scopes, instructions)

        if auto_merge:
            self._oversee_and_settle(scopes, results, task)

        return results

    # ── Scope management ──

    def open_scope(self) -> VersAgentScope:
        """Open a new isolated Vers scope for a sub-agent.

        The scope gets a fresh git worktree branched from ground. Call
        ``scope.run_agent(instruction)`` then ``scope.merge()`` or
        ``scope.discard()`` explicitly.
        """
        return self._open_scope()

    # ── Internal ──

    def _open_scope(self) -> VersAgentScope:
        unique = uuid.uuid4().hex[:8]
        scope_name = f"vers-agent-{unique}"
        branch_name = f"vers/agent/{unique}"
        worktree_dir = self._workspace.parent / f".vers-worktrees/{unique}"
        worktree_dir.mkdir(parents=True, exist_ok=True)

        # Create git worktree for the scope
        subprocess.run(
            ["git", "-C", str(self._workspace), "worktree", "add",
             "-b", branch_name, str(worktree_dir)],
            check=True,
            capture_output=True,
        )
        logger.info("opened worktree  branch=%s  dir=%s", branch_name, worktree_dir)

        scope = VersAgentScope(
            vcs=self._vcs,
            ground=self._ground,
            scope_name=scope_name,
            workspace=self._workspace,
            worktree_dir=worktree_dir,
            branch_name=branch_name,
        )
        scope._fork()
        self._active_scopes.append(scope)
        return scope

    def _run_sequential(
        self,
        scopes: list[VersAgentScope],
        instructions: list[str],
    ) -> list[SubAgentResult]:
        results = []
        for scope, instruction in zip(scopes, instructions, strict=True):
            r = scope.run_agent(instruction, timeout=self._agent_timeout)
            results.append(r)
        return results

    def _run_parallel(
        self,
        scopes: list[VersAgentScope],
        instructions: list[str],
    ) -> list[SubAgentResult]:
        results: list[SubAgentResult] = [None] * len(scopes)  # type: ignore[list-item]
        with ThreadPoolExecutor(max_workers=len(scopes)) as pool:
            futures = {
                pool.submit(scope.run_agent, instr, timeout=self._agent_timeout): idx
                for idx, (scope, instr) in enumerate(zip(scopes, instructions, strict=True))
            }
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.error("agent %d raised: %s", idx, exc)
                    results[idx] = SubAgentResult(
                        scope_name=scopes[idx].scope_name,
                        instruction=instructions[idx],
                        success=False,
                        evaluation=f"Agent raised exception: {exc}",
                    )
        return results

    def _oversee_and_settle(
        self,
        scopes: list[VersAgentScope],
        results: list[SubAgentResult],
        original_task: str,
    ) -> None:
        """Overseer LLM evaluates each diff and decides merge vs discard."""
        for scope, result in zip(scopes, results, strict=True):
            if not result.success or not result.changed_paths:
                logger.info("scope=%s  no usable output — discarding", scope.scope_name)
                result.evaluation = "Agent produced no changes or failed; discarded."
                scope.discard()
                continue

            evaluation, accept = self._evaluate_result(result, original_task)
            result.evaluation = evaluation

            if accept:
                logger.info("scope=%s  ACCEPTED — merging", scope.scope_name)
                scope.merge()
            else:
                logger.info("scope=%s  REJECTED — discarding", scope.scope_name)
                scope.discard()

    def _evaluate_result(self, result: SubAgentResult, task: str) -> tuple[str, bool]:
        """Ask the local overseer LLM to evaluate a sub-agent's diff.

        Returns (reasoning_text, accept_bool).
        """
        diff_excerpt = result.diff_text[:4000] if result.diff_text else "(no diff)"
        paths = ", ".join(result.changed_paths) if result.changed_paths else "(none)"
        prompt = textwrap.dedent(f"""
            You are the Shepherd overseer reviewing a sub-agent's work.

            ORIGINAL TASK:
            {task}

            AGENT INSTRUCTION:
            {result.instruction}

            FILES CHANGED:
            {paths}

            DIFF (truncated to 4000 chars):
            {diff_excerpt}

            AGENT STATUS: {'success' if result.success else 'FAILED (exit code ' + str(result.exit_code) + ')'}

            Evaluate whether this change should be MERGED to the main branch or DISCARDED.
            Respond with:
            DECISION: MERGE  or  DECISION: DISCARD
            REASON: <one or two sentences>
        """).strip()

        try:
            reasoning = self._call_overseer_llm(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("overseer LLM call failed: %s — defaulting to DISCARD", exc)
            return f"LLM call failed: {exc}", False

        accept = "DECISION: MERGE" in reasoning.upper()
        return reasoning, accept

    def _plan_subtasks(self, task: str, n_agents: int) -> list[str]:
        """Ask the overseer LLM to decompose ``task`` into ``n_agents`` sub-tasks.

        Falls back to repeating the original task if the LLM is unavailable.
        """
        if n_agents == 1:
            return [task]

        prompt = textwrap.dedent(f"""
            You are the Shepherd overseer. Decompose the following task into
            exactly {n_agents} focused sub-tasks. Each sub-task should be
            independently actionable by a code agent. Output a numbered list.

            TASK: {task}
        """).strip()

        try:
            response = self._call_overseer_llm(prompt)
            lines = [
                ln.lstrip("0123456789.- ").strip()
                for ln in response.splitlines()
                if ln.strip() and ln.strip()[0].isdigit()
            ]
            if len(lines) >= n_agents:
                return lines[:n_agents]
            # Not enough lines — pad with the original task
            return (lines + [task] * n_agents)[:n_agents]
        except Exception as exc:  # noqa: BLE001
            logger.warning("task planning LLM call failed: %s — using original task", exc)
            return [task] * n_agents

    def _call_overseer_llm(self, prompt: str) -> str:
        """Synchronous call to the overseer model via litellm."""
        try:
            import litellm  # type: ignore[import-untyped,import-not-found,unused-ignore]

            response = litellm.completion(
                model=self._overseer_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
            )
            return response.choices[0].message.content or ""
        except ImportError:
            raise RuntimeError(
                "litellm is not installed. "
                "Install it with: pip install litellm"
            ) from None

    # ── Setup helpers ──

    def _ensure_git_repo(self) -> None:
        """Initialize a git repo in workspace if not already present."""
        git_dir = self._workspace / ".git"
        if not git_dir.exists():
            subprocess.run(
                ["git", "init", str(self._workspace)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(self._workspace), "commit",
                 "--allow-empty", "-m", "vers-shepherd: initial commit"],
                check=True,
                capture_output=True,
            )
            logger.info("initialized git repo in %s", self._workspace)

    def _build_vcscore(self) -> Any:
        """Construct and return a VcsCore instance for the workspace."""
        try:
            from vcs_core import (
                DeclarativeFilesystemSubstrate,
                Store,
                VcsCore,
                build_builtin_substrate_context,
            )

            store = Store(str(self._workspace / ".vcscore"))
            if store.is_empty:
                store.create_root_commit()
            ctx = build_builtin_substrate_context(store, workspace=self._workspace)
            fs = DeclarativeFilesystemSubstrate(ctx)
            return VcsCore(str(self._workspace), substrates=[fs], store=store)
        except ImportError as exc:
            raise RuntimeError(
                "vcs-core is not installed. "
                "It must be on PYTHONPATH for Vers infra integration."
            ) from exc

    # ── Status / inspection ──

    def list_scopes(self) -> list[str]:
        """Return names of all currently active scopes."""
        return [s.scope_name for s in self._active_scopes]

    def scope_diff(self, scope_name: str) -> str:
        """Return the diff for a scope by name."""
        for s in self._active_scopes:
            if s.scope_name == scope_name:
                return s.get_diff()
        return ""

    def vcs_log(self, max_count: int = 20) -> list[Any]:
        """Return the VcsCore operation log for the workspace."""
        if self._vcs is None:
            return []
        try:
            return self._vcs.log(max_count=max_count)
        except Exception as exc:  # noqa: BLE001
            logger.warning("vcs_log: %s", exc)
            return []


# ---------------------------------------------------------------------------
# Convenience async wrapper
# ---------------------------------------------------------------------------


async def run_shepherd_async(
    task: str,
    workspace: str,
    *,
    n_agents: int = 1,
    model: str = "claude-sonnet-4-5",
    overseer_model: str | None = None,
    parallel: bool = True,
) -> list[SubAgentResult]:
    """Async convenience wrapper around :class:`VersShepherd`.

    Runs the shepherd loop in a thread pool so it doesn't block the event loop.

    Example::

        import asyncio
        from shepherd.vers_backend import run_shepherd_async

        results = asyncio.run(run_shepherd_async(
            "Add input validation to the user API",
            workspace="my-repo",
            n_agents=2,
            overseer_model="ollama/llama3",
        ))
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: _sync_run(task, workspace, n_agents, model, overseer_model, parallel),
    )


def _sync_run(
    task: str,
    workspace: str,
    n_agents: int,
    model: str,
    overseer_model: str | None,
    parallel: bool,
) -> list[SubAgentResult]:
    with VersShepherd(workspace, model=model, overseer_model=overseer_model) as shepherd:
        return shepherd.run(task, n_agents=n_agents, parallel=parallel)


__all__ = [
    "SubAgentResult",
    "VersAgentScope",
    "VersShepherd",
    "run_shepherd_async",
]
