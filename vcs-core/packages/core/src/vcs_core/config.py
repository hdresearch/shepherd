"""Configuration loading and layered merge.

Two config files enforce the project/machine boundary:
- vcscore.toml (project root, committed to VCS)
- .vcscore/config.toml (inside bare Git repo, local, gitignored)

Five layers merge with key-level precedence:
  built-in defaults -> user -> project -> repo -> CLI overrides
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# TOML parsing: stdlib in 3.11+, backport for 3.10
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError as e:
        msg = "Install 'tomli' for Python <3.11: pip install tomli"
        raise ImportError(msg) from e


class SecretRef(BaseModel):
    """A config value resolved from an environment variable."""

    env: str

    def resolve(self) -> str:
        """Resolve the environment variable. Raises SubstrateNotBoundError if unset."""
        from vcs_core._errors import SubstrateNotBoundError

        val = os.environ.get(self.env)
        if val is None:
            msg = f"Environment variable {self.env!r} not set. Required by substrate configuration."
            raise SubstrateNotBoundError(msg)
        return val


class DefaultsConfig(BaseModel):
    """Global defaults section."""

    device: str | None = None


class BindingConfig(BaseModel):
    """One configured substrate binding."""

    type: str
    model_config = ConfigDict(extra="allow")

    def binding_options(self) -> dict[str, Any]:
        """Return instance-specific config excluding the binding type."""
        return self.model_dump(exclude={"type"})


class VcsCoreConfig(BaseModel):
    """Top-level configuration model."""

    min_version: int = Field(default=1, description="Minimum config format version")
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    bindings: dict[str, BindingConfig] = Field(default_factory=dict)


def load_config(
    workspace: str,
    cli_overrides: dict[str, Any] | None = None,
) -> VcsCoreConfig:
    """Load and merge configuration from all layers.

    Layer order (later wins per-key):
      built-in defaults -> user -> project -> repo -> CLI overrides
    """
    layers: list[dict[str, Any]] = [{}]  # built-in defaults (empty)

    for _label, path in _locate_config_files(workspace):
        if path.exists():
            layers.append(_parse_toml(path))

    if cli_overrides:
        layers.append(cli_overrides)

    merged = _merge_layers(layers)
    return VcsCoreConfig.model_validate(merged)


def _locate_config_files(workspace: str) -> list[tuple[str, Path]]:
    """Locate config files in precedence order (lowest to highest)."""
    files: list[tuple[str, Path]] = []

    # User config
    user_config = Path.home() / ".config" / "vcs-core" / "config.toml"
    files.append(("user", user_config))

    # Project config (committed)
    project_config = Path(workspace) / "vcscore.toml"
    files.append(("project", project_config))

    # Repo config (local, gitignored)
    repo_config = Path(workspace) / ".vcscore" / "config.toml"
    files.append(("repo", repo_config))

    return files


def _parse_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file. Raises on malformed TOML."""
    with path.open("rb") as f:
        return tomllib.load(f)


def _merge_layers(layers: list[dict[str, Any]]) -> dict[str, Any]:
    """Key-level merge: later layers override per-key, not per-section.

    A repo-level isolation-level = "serializable" overrides the project
    value for that key without discarding the project-level dsn.
    """
    result: dict[str, Any] = {}
    for layer in layers:
        _deep_merge(result, layer)
    return result


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Recursively merge override into base, key by key."""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
