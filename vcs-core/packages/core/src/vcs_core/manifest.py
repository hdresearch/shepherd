"""Substrate packaging and activation-policy metadata.

SubstrateManifest describes when and how the runtime should discover or
activate a substrate. It is intentionally separate from the substrate
SPI: manifests carry packaging/activation policy, not execution
semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

ImplementationKind = Literal["driver"]


def path_exists(p: str) -> Callable[[Path], bool]:
    """Auto-detect when a path exists in the workspace."""
    return lambda workspace: (workspace / p).exists()


@dataclass(frozen=True)
class SubstrateManifest:
    """Packaging and activation-policy metadata for a substrate.

    This object is intentionally narrow. It covers packaging/runtime
    policy such as auto-detection, dependency ordering, or daemon
    requirements. Execution semantics, containment behavior, and other
    runtime properties belong on the substrate instance or SPI.
    """

    name: str
    description: str = ""
    tier: Literal["always", "auto-detect", "explicit"] = "explicit"
    status: Literal["available", "planned"] = "available"
    auto_detect: Callable[[Path], bool] | None = None
    requires_daemon: bool = False
    depends_on: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SubstratePlugin:
    """Bundled plugin registration: substrate class plus manifest metadata.

    Third-party packages can register one object that carries both the
    substrate implementation target and its activation-policy metadata.
    """

    name: str
    substrate: tuple[str, str]
    manifest: SubstrateManifest
    implementation_kind: ImplementationKind = "driver"

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("SubstratePlugin name must be non-empty.")
        if self.manifest.name != self.name:
            raise ValueError(
                f"SubstratePlugin name must match manifest.name; got {self.name!r} vs {self.manifest.name!r}."
            )
        if self.implementation_kind != "driver":
            raise ValueError("SubstratePlugin implementation_kind must be 'driver'.")


# ---------------------------------------------------------------------------
# Built-in substrate manifests
# ---------------------------------------------------------------------------

MANIFESTS: dict[str, SubstrateManifest] = {
    "filesystem": SubstrateManifest(
        name="filesystem",
        tier="always",
        description="Filesystem sandboxing (FUSE overlay or declarative)",
    ),
    "marker": SubstrateManifest(
        name="marker",
        tier="always",
        description="Zero-ceremony annotation commits",
    ),
    "git": SubstrateManifest(
        name="git",
        tier="auto-detect",
        status="available",
        depends_on=("filesystem",),
        auto_detect=path_exists(".git"),
        description="Git command observation via subprocess interception",
    ),
    "sqlite": SubstrateManifest(
        name="sqlite",
        tier="explicit",
        status="available",
        description="Buffered SQLite execution with replayable materialization",
    ),
    "http": SubstrateManifest(
        name="http",
        tier="explicit",
        status="planned",
        requires_daemon=True,
        description="HTTP/S proxy interception and buffering",
    ),
}

# Module/class mapping for built-in substrates
BUILT_IN_SUBSTRATES: dict[str, tuple[str, str]] = {
    "filesystem": ("vcs_core.substrates", "FilesystemSubstrate"),
    "git": ("vcs_core.git_substrate", "GitSubstrate"),
    "marker": ("vcs_core.substrates", "MarkerSubstrate"),
    "sqlite": ("vcs_core.sqlite_substrate", "SQLiteSubstrate"),
}

BUILT_IN_PLUGINS: dict[str, SubstratePlugin] = {
    name: SubstratePlugin(
        name=name,
        substrate=substrate,
        manifest=MANIFESTS[name],
        implementation_kind="driver",
    )
    for name, substrate in BUILT_IN_SUBSTRATES.items()
}
