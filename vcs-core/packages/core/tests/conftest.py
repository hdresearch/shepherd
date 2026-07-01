"""Shared test fixtures for vcs-core."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    from vcs_core.vcscore import VcsCore

from .support.builders import make_marker_filesystem_vcscore, make_store
from .support.capabilities import LocalBindCapability, probe_local_bind_capability


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """Provide a temporary directory for a bare Git repository."""
    repo_path = tmp_path / ".vcscore"
    repo_path.mkdir()
    return repo_path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Provide a temporary workspace directory."""
    return tmp_path


@pytest.fixture
def store(tmp_repo: Path):  # type: ignore[type-arg]
    """Provide an initialized Store with root commit."""
    s = make_store(tmp_repo.parent)
    s.create_root_commit()
    return s


@pytest.fixture
def mg(workspace: Path) -> VcsCore:
    """Provide an activated VcsCore with marker + filesystem substrates."""
    vcscore = make_marker_filesystem_vcscore(workspace, activate=True)
    try:
        yield vcscore
    finally:
        vcscore.deactivate()


@pytest.fixture(scope="session")
def local_bind_capability(tmp_path_factory: pytest.TempPathFactory) -> LocalBindCapability:
    """Report whether the current environment permits local listener bind()."""
    probe_dir = tmp_path_factory.mktemp("local-bind-capability")
    return probe_local_bind_capability(probe_dir)


@pytest.fixture
def requires_local_bind(local_bind_capability: LocalBindCapability) -> LocalBindCapability:
    """Skip tests that require real local listener sockets when unavailable."""
    if not local_bind_capability.available:
        pytest.skip(local_bind_capability.reason or "local listener bind is unavailable")
    return local_bind_capability
