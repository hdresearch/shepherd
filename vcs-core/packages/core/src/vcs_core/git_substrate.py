"""Observe-only Git substrate.

This substrate captures Git command semantics without taking ownership of
workspace isolation. Filesystem effects still belong to the filesystem
substrate. GitSubstrate only records higher-level Git operations.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vcs_core._hooks import HookEffects, HookEvent, SystemHook
from vcs_core._substrate_runtime import (
    BuiltInRuntimeBinding,
    BuiltInSubstrateContext,
    PerformedEventSpec,
    PythonPatch,
    bootstrap_builtin_runtime,
)
from vcs_core.authority import SubstrateAuthority, make_authority_aspect
from vcs_core.spi import (
    CapabilitySet,
    CommandRequest,
    CommandSpec,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    IngressRequest,
    ParamSpec,
    UnsupportedRequestError,
)
from vcs_core.types import EffectRecord, ScopeInfo

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from vcs_core.materialization import InternalMaterializer


class GitSubstrate:
    """Observe-only Git command substrate."""

    name = "git"
    binding = "git"
    role = "git"
    driver_id = "git"
    driver_version = "v1"

    @property
    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(accepts=frozenset({CommandRequest}), selectable=False)

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            commands=self.commands,
        )

    @property
    def commands(self) -> dict[str, CommandSpec]:
        return {
            "commit": CommandSpec(
                description="Create a Git commit in the workspace repository.",
                params={"message": ParamSpec(type="str", description="Commit message.")},
                examples=("vcs-core exec git commit -p message='checkpoint'",),
            ),
            "branch": CommandSpec(
                description="Create a Git branch in the workspace repository.",
                params={
                    "name": ParamSpec(type="str", description="Branch name."),
                    "start_point": ParamSpec(
                        type="str",
                        required=False,
                        description="Optional starting revision for the branch.",
                    ),
                },
                examples=("vcs-core exec git branch -p name=feature/demo",),
            ),
            "checkout": CommandSpec(
                description="Switch the Git working copy to a branch or revision.",
                params={"ref": ParamSpec(type="str", description="Branch or revision to check out.")},
                examples=("vcs-core exec git checkout -p ref=main",),
            ),
            "status": CommandSpec(
                description="Observe Git working tree status.",
                params={},
                examples=("vcs-core exec git status",),
            ),
        }

    def __init__(self, ctx: BuiltInSubstrateContext) -> None:
        runtime, workspace = bootstrap_builtin_runtime(ctx)
        self._workspace = workspace
        self.bind_runtime(runtime)

    def bind_runtime(self, binding: BuiltInRuntimeBinding) -> None:
        self._runtime = binding
        self._pipeline = binding.pipeline

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def materializers(self) -> Sequence[InternalMaterializer]:
        return ()

    def push(self, scope_id: str | None = None) -> None:
        del scope_id

    def authority(self) -> SubstrateAuthority:
        return SubstrateAuthority(
            substrate=self.name,
            containment=make_authority_aspect(
                regime="none",
                access_gated=False,
                tier="python",
                reason="Git subprocess interception does not gate or isolate repository access.",
            ),
            provenance=make_authority_aspect(
                regime="partial",
                access_gated=False,
                tier="python",
                reason=(
                    "Git capture combines Python subprocess interception with session-shell PATH wrappers, "
                    "but remains bypassable via absolute-path execution, non-session processes, and direct .git mutation."
                ),
            ),
            reason=(
                "Git capture combines Python subprocess interception with session-shell PATH wrappers, "
                "but remains bypassable via absolute-path execution, non-session processes, and direct .git mutation."
            ),
        )

    def python_patches(self) -> Sequence[PythonPatch]:
        return (
            PythonPatch(
                target="subprocess.run",
                after_translator=self._translate_subprocess_run,
                path_candidates=self._subprocess_path_candidates,
            ),
        )

    def system_hooks(self) -> Sequence[SystemHook]:
        return (
            SystemHook(
                hook_id="git-cli",
                kind="path_wrapper",
                config={"binary": "git"},
                translator=self._translate_path_wrapper_event,
            ),
        )

    def prepare(self, context: DriverContext, request: IngressRequest) -> DriverIngressResult:
        del context
        if not isinstance(request, CommandRequest):
            raise UnsupportedRequestError(driver_id=self.driver_id, request_type=type(request))
        return self.execute(request.command, self._pipeline.require_world(), **dict(request.params))

    def capture_adapters(self, context: DriverContext) -> tuple[Any, ...]:
        del context
        return ()

    def validate_result(self, request: IngressRequest, result: DriverIngressResult) -> None:
        del request, result

    def execute(
        self,
        command: str,
        scope: ScopeInfo,
        **params: Any,
    ) -> DriverIngressResult:
        repo_cwd = self._command_cwd(params, scope=scope)

        if command == "status":
            return DriverIngressResult(effects=(self._build_status_effect(repo_cwd),))

        if command == "commit":
            argv = ["git", "commit", "-m", params["message"]]
            self._run_git(argv, cwd=repo_cwd)
            return DriverIngressResult(effects=tuple(self._effects_from_invocation(argv, cwd=repo_cwd)))

        if command == "branch":
            argv = ["git", "branch", params["name"]]
            if params.get("start_point"):
                argv.append(params["start_point"])
            self._run_git(argv, cwd=repo_cwd)
            return DriverIngressResult(effects=tuple(self._effects_from_invocation(argv, cwd=repo_cwd)))

        if command == "checkout":
            argv = ["git", "checkout", params["ref"]]
            self._run_git(argv, cwd=repo_cwd)
            return DriverIngressResult(effects=tuple(self._effects_from_invocation(argv, cwd=repo_cwd)))

        raise ValueError(f"Unknown git command: {command!r}")

    def performed_event_specs(self) -> dict[str, PerformedEventSpec]:
        optional_cwd = ParamSpec(type="str", required=False, projectable=False)
        return {
            "commit": PerformedEventSpec(
                description="A Git commit was created.",
                params={
                    "message": ParamSpec(type="str"),
                    "_cwd": optional_cwd,
                    "_sha": ParamSpec(type="str", required=False, projectable=False),
                    "_branch": ParamSpec(type="str", required=False, projectable=False),
                },
                effect_types=("GitCommitCreated",),
            ),
            "branch": PerformedEventSpec(
                description="A Git branch was created.",
                params={
                    "name": ParamSpec(type="str"),
                    "start_point": ParamSpec(type="str", required=False),
                    "_cwd": optional_cwd,
                },
                effect_types=("GitBranchCreated",),
            ),
            "checkout": PerformedEventSpec(
                description="A Git checkout or switch completed.",
                params={
                    "ref": ParamSpec(type="str"),
                    "_cwd": optional_cwd,
                    "_branch": ParamSpec(type="str", required=False, projectable=False),
                },
                effect_types=("GitCheckout",),
            ),
            "status": PerformedEventSpec(
                description="Git status was observed.",
                params={
                    "_cwd": optional_cwd,
                    "_branch": ParamSpec(type="str", required=False, projectable=False),
                    "_clean": ParamSpec(type="bool", required=False, projectable=False),
                    "_summary": ParamSpec(type="str?", required=False, projectable=False),
                },
                effect_types=("GitStatusObserved",),
            ),
        }

    def performed_effects(
        self,
        event: str,
        scope: ScopeInfo,
        *,
        params: Mapping[str, Any],
    ) -> Sequence[EffectRecord]:
        return (self._performed_effect(event, cwd=self._command_cwd(dict(params), scope=scope), params=dict(params)),)

    def _subprocess_path_candidates(self, *args: Any, **kwargs: Any) -> tuple[Path, ...]:
        del args
        cwd = kwargs.get("cwd")
        if cwd is None:
            return (Path.cwd(),)
        return (Path(cwd),)

    def _translate_subprocess_run(
        self,
        args: object,
        *popenargs: Any,
        _result: object = None,
        **kwargs: Any,
    ) -> tuple[str, dict[str, Any]] | None:
        del popenargs, _result
        argv = self._normalize_argv(args)
        if argv is None:
            return None
        try:
            return self._classify_invocation(argv, cwd=self._command_cwd(kwargs))
        except ValueError:
            return None

    def _translate_path_wrapper_event(self, event: HookEvent) -> HookEffects | None:
        if event.phase != "finish" or event.exit_code != 0 or not event.cwd:
            return None
        try:
            translated = self._classify_invocation(list(event.argv), cwd=Path(event.cwd))
        except ValueError:
            return None
        if translated is None:
            return None
        command, params = translated
        cwd = Path(event.cwd)
        return HookEffects(effects=(self._performed_effect(command, cwd=cwd, params=params),))

    def _effects_from_invocation(self, argv: list[str], *, cwd: Path) -> list[EffectRecord]:
        translated = self._classify_invocation(argv, cwd=cwd)
        if translated is None:
            raise ValueError(f"Unsupported git invocation for substrate recording: {' '.join(argv)}")
        command, params = translated
        effect = self._performed_effect(command, cwd=cwd, params=params)
        return [effect]

    def _classify_invocation(self, argv: list[str], *, cwd: Path) -> tuple[str, dict[str, Any]] | None:
        if len(argv) < 2 or Path(argv[0]).name != "git":
            return None

        subcommand = argv[1]
        if subcommand == "commit":
            message = self._extract_commit_message(argv)
            if message is None:
                return None
            return (
                "commit",
                {
                    "message": message,
                    "_cwd": str(cwd),
                    "_sha": self._read_head_sha(cwd),
                    "_branch": self._read_current_branch(cwd),
                },
            )

        if subcommand == "branch":
            branch_params = self._extract_branch_params(argv)
            if branch_params is None:
                return None
            branch_params["_cwd"] = str(cwd)
            return ("branch", branch_params)

        if subcommand in {"checkout", "switch"}:
            ref = self._extract_checkout_ref(argv)
            if ref is None:
                return None
            return (
                "checkout",
                {
                    "ref": ref,
                    "_cwd": str(cwd),
                    "_branch": self._read_current_branch(cwd),
                },
            )

        if subcommand == "status":
            branch, clean, summary = self._status_snapshot(cwd)
            return (
                "status",
                {
                    "_cwd": str(cwd),
                    "_branch": branch,
                    "_clean": clean,
                    "_summary": summary,
                },
            )

        return None

    def _performed_effect(self, command: str, *, cwd: Path, params: dict[str, Any]) -> EffectRecord:
        if command == "commit":
            return self._build_commit_effect(cwd, params=params)
        if command == "branch":
            return self._build_branch_effect(params)
        if command == "checkout":
            return self._build_checkout_effect(cwd, params=params)
        if command == "status":
            return self._build_status_effect(cwd, params=params)
        raise ValueError(f"Unknown git command: {command!r}")

    def _normalize_argv(self, args: object) -> list[str] | None:
        if isinstance(args, (list, tuple)):
            argv: list[str] = []
            for arg in args:
                try:
                    argv.append(os.fspath(arg))
                except TypeError:
                    return None
            return argv
        return None

    def _command_cwd(self, params: dict[str, Any], *, scope: ScopeInfo | None = None) -> Path:
        raw_cwd = params.get("_cwd")
        if raw_cwd is None:
            if scope is not None:
                return self._runtime.working_directory_for_scope(scope)
            return self._workspace
        return Path(os.fspath(raw_cwd)).resolve()

    def _run_git(self, argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate()
        result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
        if result.returncode != 0:
            detail = (stderr or stdout).strip() or f"exit code {result.returncode}"
            raise ValueError(f"Git command failed ({' '.join(argv)}): {detail}")
        return result

    def _read_head_sha(self, cwd: Path) -> str:
        return self._run_git(["git", "rev-parse", "HEAD"], cwd=cwd).stdout.strip()

    def _read_current_branch(self, cwd: Path) -> str:
        return self._run_git(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()

    def _status_snapshot(self, cwd: Path) -> tuple[str, bool, str | None]:
        lines = self._run_git(["git", "status", "--porcelain", "--branch"], cwd=cwd).stdout.splitlines()
        branch_line = lines[0] if lines else "## HEAD"
        branch = branch_line.removeprefix("## ").split("...", 1)[0].strip()
        summary_lines = lines[1:] if lines and branch_line.startswith("## ") else lines
        summary_lines = [line for line in summary_lines if not self._is_internal_status_line(line)]
        summary = "\n".join(summary_lines) if summary_lines else None
        return branch, not summary_lines, summary

    def _is_internal_status_line(self, line: str) -> bool:
        return any(self._is_internal_status_path(path) for path in self._status_entry_paths(line))

    def _status_entry_paths(self, line: str) -> tuple[str, ...]:
        payload = line[3:] if len(line) > 3 else ""
        if " -> " in payload:
            return tuple(self._normalize_status_path(part) for part in payload.split(" -> "))
        return (self._normalize_status_path(payload),)

    def _normalize_status_path(self, raw_path: str) -> str:
        path = raw_path.strip()
        if len(path) >= 2 and path[0] == path[-1] == '"':
            return bytes(path[1:-1], "utf-8").decode("unicode_escape")
        return path

    def _is_internal_status_path(self, path: str) -> bool:
        return path == ".vcscore" or path.startswith(".vcscore/")

    def _extract_commit_message(self, argv: list[str]) -> str | None:
        for index, token in enumerate(argv[2:], start=2):
            if token in {"-m", "--message"} and index + 1 < len(argv):
                return argv[index + 1]
            if token.startswith("--message="):
                return token.split("=", 1)[1]
        return None

    def _extract_branch_params(self, argv: list[str]) -> dict[str, Any] | None:
        if any(token in {"-d", "-D", "-m", "-M", "--delete", "--move"} for token in argv[2:]):
            return None
        positional = [token for token in argv[2:] if not token.startswith("-")]
        if not positional:
            return None
        params: dict[str, Any] = {"name": positional[0]}
        if len(positional) > 1:
            params["start_point"] = positional[1]
        return params

    def _extract_checkout_ref(self, argv: list[str]) -> str | None:
        if "--" in argv[2:] or any(
            token in {"-b", "-B", "-c", "-C", "--orphan", "--create", "--force-create"} for token in argv[2:]
        ):
            return None
        positional = [token for token in argv[2:] if not token.startswith("-")]
        if not positional:
            return None
        return positional[0]

    def _build_commit_effect(self, cwd: Path, *, params: dict[str, Any]) -> EffectRecord:
        metadata: dict[str, Any] = {
            "sha": params.get("_sha") or self._read_head_sha(cwd),
            "message": params["message"],
        }
        branch = params.get("_branch") or self._read_current_branch(cwd)
        if branch:
            metadata["branch"] = branch
        return EffectRecord(effect_type="GitCommitCreated", metadata=metadata)

    def _build_branch_effect(self, params: dict[str, Any]) -> EffectRecord:
        metadata: dict[str, Any] = {"name": params["name"]}
        if params.get("start_point"):
            metadata["start_point"] = params["start_point"]
        return EffectRecord(effect_type="GitBranchCreated", metadata=metadata)

    def _build_checkout_effect(self, cwd: Path, *, params: dict[str, Any]) -> EffectRecord:
        branch = params.get("_branch") or self._read_current_branch(cwd)
        return EffectRecord(
            effect_type="GitCheckout",
            metadata={
                "ref": params["ref"],
                "branch": branch,
                "detached": branch == "HEAD",
            },
        )

    def _build_status_effect(self, cwd: Path, *, params: dict[str, Any] | None = None) -> EffectRecord:
        params = params or {}
        branch = params.get("_branch")
        clean = params.get("_clean")
        summary = params.get("_summary")
        if branch is None or clean is None:
            branch, clean, summary = self._status_snapshot(cwd)

        metadata: dict[str, Any] = {
            "branch": branch,
            "clean": bool(clean),
        }
        if summary:
            metadata["summary"] = summary
        return EffectRecord(effect_type="GitStatusObserved", metadata=metadata)
