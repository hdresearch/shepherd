"""Configuration-related CLI command group."""

from __future__ import annotations

import sys
from collections.abc import Mapping, MutableMapping
from pathlib import Path

import click


@click.group("config")
def config_group() -> None:
    """Manage configuration."""


@config_group.command("list")
@click.argument("prefix", required=False)
def config_list(prefix: str | None) -> None:
    """Show config values with source annotations."""
    from vcs_core.config import load_config

    config = load_config(".")
    flat = _flatten_dict(config.model_dump())

    for key, value in sorted(flat.items()):
        if prefix and not key.startswith(prefix):
            continue
        source = _find_source(key, ".")
        click.echo(f"{key} = {value!r:<40s}  # {source}")


@config_group.command("set")
@click.option("--user", "layer", flag_value="user", help="Set in user config")
@click.option("--repo", "layer", flag_value="repo", help="Set in repo config")
@click.option("--project", "layer", flag_value="project", help="Set in project config")
@click.argument("key")
@click.argument("value")
def config_set(layer: str | None, key: str, value: str) -> None:
    """Set a config value in a specific layer."""
    if not layer:
        click.echo("Error: specify --user, --repo, or --project")
        sys.exit(1)

    import tomli_w

    config_path = _layer_path(layer)
    config_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, object] = {}
    if config_path.exists():
        import tomllib

        with config_path.open("rb") as f:
            existing = tomllib.load(f)

    keys = key.split(".")
    _seed_binding_type(existing, keys)
    _set_nested(existing, keys, value)
    config_path.write_bytes(tomli_w.dumps(existing).encode())
    click.echo(f"Set {key} = {value!r} in {config_path}.")


@config_group.command("path")
@click.option("--user", "layer", flag_value="user")
@click.option("--repo", "layer", flag_value="repo")
@click.option("--project", "layer", flag_value="project")
def config_path(layer: str | None) -> None:
    """Print config file path."""
    if not layer:
        click.echo("Error: specify --user, --repo, or --project")
        sys.exit(1)
    click.echo(str(_layer_path(layer)))


def _layer_path(layer: str) -> Path:
    if layer == "user":
        return Path.home() / ".config" / "vcs-core" / "config.toml"
    if layer == "repo":
        return Path(".vcscore") / "config.toml"
    return Path("vcscore.toml")


def _flatten_dict(data: Mapping[str, object], prefix: str = "") -> dict[str, object]:
    """Flatten a nested dict into dot-separated keys."""
    items: dict[str, object] = {}
    for key, value in data.items():
        flat_key = f"{prefix}{key}" if not prefix else f"{prefix}.{key}"
        if isinstance(value, dict):
            items.update(_flatten_dict(value, flat_key))
        else:
            items[flat_key] = value
    return items


def _find_source(key: str, workspace: str) -> str:
    """Find which config layer provides a key."""
    from vcs_core.config import _locate_config_files, _parse_toml

    for _label, path in reversed(_locate_config_files(workspace)):
        if path.exists():
            data = _parse_toml(path)
            flat = _flatten_dict(data)
            if key in flat:
                return str(path)
    return "(default)"


def _set_nested(data: MutableMapping[str, object], keys: list[str], value: str) -> None:
    """Set a value in a nested dict via key path."""
    for key in keys[:-1]:
        child = data.get(key)
        if not isinstance(child, MutableMapping):
            child = {}
            data[key] = child
        data = child
    data[keys[-1]] = value


def _seed_binding_type(data: MutableMapping[str, object], keys: list[str]) -> None:
    """Seed ``bindings.<name>.type`` for partial updates when alias == substrate type."""
    if len(keys) < 3 or keys[0] != "bindings" or keys[2] == "type":
        return

    binding_name = keys[1]
    bindings = data.get("bindings")
    if not isinstance(bindings, MutableMapping):
        bindings = {}
        data["bindings"] = bindings
    binding = bindings.get(binding_name)
    if not isinstance(binding, MutableMapping):
        binding = {}
        bindings[binding_name] = binding
    if "type" in binding:
        return

    from vcs_core.discovery import discover_manifests

    if binding_name in discover_manifests(strict=False):
        binding["type"] = binding_name
