"""Internal built-in substrate runtime contracts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from vcs_core._claims import ResourceClaim
    from vcs_core._hooks import SystemHook
    from vcs_core._patch_paths import PatchPathCandidateLike
    from vcs_core._substrate_driver import ParamSpec
    from vcs_core.materialization import InternalMaterializer
    from vcs_core.recording import RecordingPipeline
    from vcs_core.types import EffectRecord, FileState, ScopeInfo

PatchMutationIntent: TypeAlias = Literal["none", "external_write"]
PatchMutationIntentClassifier: TypeAlias = Callable[..., PatchMutationIntent]


@dataclass(frozen=True)
class PythonPatch:
    """Declarative description of an internal Python interception point."""

    target: str
    after_translator: Callable[..., tuple[str, dict[str, Any]] | None] | None = None
    wrap_handler: Callable[..., Any] | None = None
    path_candidates: Callable[..., Sequence[PatchPathCandidateLike]] | None = None
    requires_scope: bool = False
    mutation_intent: PatchMutationIntent | PatchMutationIntentClassifier = "none"

    def __post_init__(self) -> None:
        if (self.after_translator is None) == (self.wrap_handler is None):
            msg = "PythonPatch requires exactly one of after_translator or wrap_handler."
            raise ValueError(msg)
        if isinstance(self.mutation_intent, str) and self.mutation_intent not in {"none", "external_write"}:
            msg = f"Unsupported PythonPatch mutation_intent: {self.mutation_intent!r}."
            raise ValueError(msg)

    @property
    def when(self) -> str:
        return "wrap" if self.wrap_handler is not None else "after"


@dataclass(frozen=True)
class BuiltInRuntimeBinding:
    """Internal coordinator-owned runtime services for built-in substrates."""

    pipeline: RecordingPipeline
    is_scope_or_ancestor_isolated: Callable[[ScopeInfo], bool]
    overlay_base_scope_name: Callable[[ScopeInfo], str]
    working_directory_for_scope: Callable[[ScopeInfo], Path]
    parent_scope: Callable[[ScopeInfo], ScopeInfo | None] = field(default=lambda _scope: None)
    lookup_scope: Callable[[str], ScopeInfo | None] = field(default=lambda _name: None)
    nearest_carrier_scope: Callable[[str, str, ScopeInfo], ScopeInfo | None] = field(
        default=lambda _substrate, _target_id, _scope: None
    )
    can_create_carrier: Callable[[str, str, ScopeInfo], bool] = field(
        default=lambda _substrate, _target_id, _scope: True
    )
    register_carrier: Callable[[str, str, ScopeInfo], None] = field(default=lambda _substrate, _target_id, _scope: None)
    lookup_claim: Callable[[str | Path], ResourceClaim | None] = field(default=lambda _path: None)
    register_claim: Callable[[str, str, str | Path, str], ResourceClaim | None] = field(
        default=lambda _substrate, _target_id, _path, _policy: None
    )
    control_plane_guard: Callable[[], Any] = field(default=lambda: nullcontext())
    # Tranche 3 byte-source hook for tree-backed workspace materialization.
    # Returns ``(content, filemode)`` when the ground world's workspace head is
    # tree-backed and the path resolves in its embedded ``workspace/`` tree.
    # Returns ``None`` when no v2 state exists, the selected revision is
    # digest-only, or the path is not present in the substrate tree; the
    # filesystem substrate then falls back to scalar coord reads.
    ground_workspace_byte_source: Callable[[str], tuple[bytes, int] | None] = field(default=lambda _path: None)
    # Companion query for the byte source: returns ``True`` when the ground
    # world's workspace head is tree-backed. The filesystem substrate calls
    # this once per materialization (before the per-file loop) so it can emit
    # a diagnostic warning when ``ground_workspace_byte_source`` returns
    # ``None`` for a diff path despite the ground being tree-backed - a state
    # Tranche 1's manifest/tree correspondence validator should make
    # unreachable. The warning is observability, not enforcement.
    ground_workspace_is_tree_backed: Callable[[], bool] = field(default=lambda: False)


@dataclass(frozen=True)
class BuiltInSubstrateContext:
    """Internal construction context for framework-owned substrates."""

    store: Any
    workspace: Path
    config: dict[str, Any] = field(default_factory=dict)


def build_builtin_substrate_context(
    store: Any,
    *,
    workspace: Path | None = None,
    config: dict[str, Any] | None = None,
) -> BuiltInSubstrateContext:
    """Create the internal built-in substrate context from framework state."""
    repo_path = getattr(store, "repo_path", None)
    resolved_workspace = workspace
    if resolved_workspace is None:
        if repo_path is None:
            msg = "workspace is required when store does not expose repo_path."
            raise TypeError(msg)
        resolved_workspace = Path(str(repo_path)).parent
    return BuiltInSubstrateContext(
        store=store,
        workspace=resolved_workspace.resolve(),
        config=dict(config or {}),
    )


def default_runtime_binding(
    pipeline: RecordingPipeline,
    *,
    workspace: Path | None = None,
) -> BuiltInRuntimeBinding:
    """Create a no-op runtime binding for direct built-in construction."""
    resolved_workspace = (workspace or Path.cwd()).resolve()
    return BuiltInRuntimeBinding(
        pipeline=pipeline,
        control_plane_guard=lambda: nullcontext(),
        is_scope_or_ancestor_isolated=lambda _scope: False,
        overlay_base_scope_name=lambda _scope: "ground",
        working_directory_for_scope=lambda _scope: resolved_workspace,
    )


def bootstrap_builtin_runtime(
    ctx: object,
) -> tuple[BuiltInRuntimeBinding, Path]:
    """Create the default runtime binding used during direct built-in construction."""
    from vcs_core.recording import RecordingPipeline

    if not isinstance(ctx, BuiltInSubstrateContext):
        msg = "built-in substrates require a BuiltInSubstrateContext."
        raise TypeError(msg)
    workspace = ctx.workspace.resolve()
    pipeline = RecordingPipeline(ctx.store)
    return default_runtime_binding(pipeline, workspace=workspace), workspace


@runtime_checkable
class RuntimeBoundSubstrate(Protocol):
    """Internal coordinator binding seam for built-in substrates."""

    def bind_runtime(self, binding: BuiltInRuntimeBinding) -> None: ...


@runtime_checkable
class ContainmentSubstrate(Protocol):
    """Internal per-scope lifecycle seam for built-in substrates.

    `prepare_merge()` must be replay-safe for the same scope state while
    a merge or discard lifecycle run is being resumed. In practice that
    means repeated calls before `commit_merge()` / `discard()` should
    return a stable effect prefix so lifecycle recovery can resume from a
    persisted count without changing durable history.
    """

    name: str

    def branch(self, scope_id: str, *, parent_scope: ScopeInfo, hints: dict[str, Any] | None = None) -> None: ...

    def prepare_merge(self, scope: ScopeInfo, parent: ScopeInfo) -> Sequence[EffectRecord]: ...

    def commit_merge(self, scope_id: str, *, parent_scope: ScopeInfo) -> None: ...

    def discard(self, scope_id: str) -> None: ...


@runtime_checkable
class RetainedRuntimeCloseSubstrate(Protocol):
    """Optional seal lifecycle hook for closing retained child runtime state."""

    name: str

    def close_retained(self, scope_id: str, *, parent_scope: ScopeInfo) -> None: ...


@runtime_checkable
class CarrierBackend(Protocol):
    """Internal reversibility-axis backend contract — the **Carrier**.

    (containment-and-carriers.md §2): a non-destructive working layer that can be
    created, written, diffed, committed, or discarded, so a run is reversible (merge
    on success / discard on failure).

    `working_path(scope_id)` is the carrier-neutral working tree for a scope — an overlay
    mount (fuse/kernel) or a clonefile clone directory.
    """

    def create_layer(self, scope_id: str, *, parent_scope_id: str | None) -> None: ...

    def has_layer(self, scope_id: str) -> bool: ...

    def read_file(self, scope_id: str, path: str) -> bytes: ...

    def read_file_state(self, scope_id: str, path: str) -> FileState: ...

    def write_file(self, scope_id: str, path: str, content: bytes, *, mode: int = 0o100644) -> None: ...

    def delete_file(self, scope_id: str, path: str) -> None: ...

    def diff_layer(self, scope_id: str) -> list[tuple[str, bytes | None, int]]: ...

    def commit_layer(self, scope_id: str, *, into_scope_id: str | None) -> None: ...

    def discard_layer(self, scope_id: str) -> None: ...

    def push_layer(self, scope_id: str | None = None) -> None: ...

    def working_path(self, scope_id: str) -> Path: ...

    def deactivate(self) -> None: ...


@runtime_checkable
class InternalMaterializerProvider(Protocol):
    """Internal planner-owned materializer registration seam."""

    def materializers(self) -> Sequence[InternalMaterializer]: ...


@runtime_checkable
class PythonPatchProvider(Protocol):
    """Internal Python interception seam."""

    def python_patches(self) -> Sequence[PythonPatch]: ...


@runtime_checkable
class PerformedEventProvider(Protocol):
    """Internal already-performed event seam."""

    name: str

    def performed_event_specs(self) -> Mapping[str, PerformedEventSpec]: ...

    def performed_effects(
        self,
        event: str,
        scope: ScopeInfo,
        *,
        params: Mapping[str, Any],
    ) -> Sequence[EffectRecord]: ...


@dataclass(frozen=True)
class PerformedEventSpec:
    """Declared params and output effects for an already-performed event."""

    description: str = ""
    params: Mapping[str, ParamSpec] = field(default_factory=dict)
    examples: tuple[str, ...] = ()
    effect_types: tuple[str, ...] = ()
    allow_unknown_params: bool = False


@runtime_checkable
class SystemHookProvider(Protocol):
    """Internal system hook declaration seam."""

    def system_hooks(self) -> Sequence[SystemHook]: ...
