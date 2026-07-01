"""Baseline substrate discovery and resolution tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from vcs_core.config import VcsCoreConfig
from vcs_core.discovery import (
    SubstrateResolutionError,
    check_substrate,
    discover_manifests,
    discover_substrates,
    resolve_bindings,
)
from vcs_core.store import Store

if TYPE_CHECKING:
    from pathlib import Path


def test_discover_built_in_substrates() -> None:
    available = discover_substrates()
    assert "filesystem" in available
    assert "git" in available
    assert "marker" in available


def test_discover_built_in_manifests() -> None:
    manifests = discover_manifests()
    assert "filesystem" in manifests
    assert manifests["filesystem"].tier == "always"


def test_discover_manifest_catalog_includes_available_and_planned_built_ins() -> None:
    manifests = discover_manifests()
    assert "git" in manifests
    assert manifests["git"].status == "available"
    assert "http" in manifests
    assert manifests["http"].status == "planned"


def test_resolve_always_active(tmp_path: Path) -> None:
    repo_path = tmp_path / ".vcscore"
    repo_path.mkdir()
    store = Store(str(repo_path))
    store.create_root_commit()

    config = VcsCoreConfig()
    bindings = resolve_bindings(config, tmp_path, store)
    names = [binding.binding_name for binding in bindings]
    assert "filesystem" in names
    assert "marker" in names


def test_resolve_auto_detected(tmp_path: Path) -> None:
    repo_path = tmp_path / ".vcscore"
    repo_path.mkdir()
    store = Store(str(repo_path))
    store.create_root_commit()

    (tmp_path / ".git").mkdir()

    config = VcsCoreConfig()
    bindings = resolve_bindings(config, tmp_path, store)
    names = [binding.binding_name for binding in bindings]
    assert "filesystem" in names
    assert "git" in names
    assert "marker" in names


def test_resolve_missing_dependency(tmp_path: Path) -> None:
    repo_path = tmp_path / ".vcscore"
    repo_path.mkdir()
    store = Store(str(repo_path))
    store.create_root_commit()

    config = VcsCoreConfig(bindings={"nonexistent": {"type": "nonexistent", "key": "val"}})
    with pytest.raises(SubstrateResolutionError, match="not installed"):
        resolve_bindings(config, tmp_path, store)


def test_resolve_known_planned_substrate_reports_not_implemented(tmp_path: Path) -> None:
    repo_path = tmp_path / ".vcscore"
    repo_path.mkdir()
    store = Store(str(repo_path))
    store.create_root_commit()

    config = VcsCoreConfig(bindings={"http": {"type": "http"}})
    with pytest.raises(SubstrateResolutionError, match="not yet implemented"):
        resolve_bindings(config, tmp_path, store)


def test_resolve_secret_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_path = tmp_path / ".vcscore"
    repo_path.mkdir()
    store = Store(str(repo_path))
    store.create_root_commit()

    monkeypatch.delenv("NONEXISTENT_DB_URL", raising=False)
    config = VcsCoreConfig(bindings={"filesystem": {"type": "filesystem", "dsn": {"env": "NONEXISTENT_DB_URL"}}})
    with pytest.raises(SubstrateResolutionError, match="NONEXISTENT_DB_URL"):
        resolve_bindings(config, tmp_path, store)


def test_check_substrate_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_DSN", "postgresql://localhost/test")
    config = VcsCoreConfig(bindings={"filesystem": {"type": "filesystem", "dsn": {"env": "TEST_DSN"}}})
    results = check_substrate("filesystem", config, tmp_path)
    assert results["config"] == "valid"
    assert results["secret:dsn"] == "resolved"


def test_check_substrate_secret_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    config = VcsCoreConfig(bindings={"filesystem": {"type": "filesystem", "dsn": {"env": "MISSING_VAR"}}})
    results = check_substrate("filesystem", config, tmp_path)
    assert "FAILED" in results["secret:dsn"]


def test_check_substrate_binding_alias_uses_binding_type_manifest(tmp_path: Path) -> None:
    config = VcsCoreConfig(bindings={"repo_git": {"type": "git"}})

    results = check_substrate("repo_git", config, tmp_path)

    assert results["config"] == "valid"
    assert results["dependency:filesystem"] == "satisfied"
