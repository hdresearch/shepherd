"""Dependency ordering and cycle detection tests for discovery."""

from __future__ import annotations

import pytest
from vcs_core.discovery import SubstrateResolutionError, _topological_sort


def test_topological_sort() -> None:
    result = _topological_sort({"filesystem", "marker", "git"})
    assert result.index("filesystem") < result.index("git")


def test_topological_sort_no_deps() -> None:
    result = _topological_sort({"filesystem", "marker"})
    assert set(result) == {"filesystem", "marker"}


def test_topological_sort_detects_cycle() -> None:
    from vcs_core.manifest import MANIFESTS, SubstrateManifest

    MANIFESTS["cycle-a"] = SubstrateManifest(name="cycle-a", depends_on=["cycle-b"])
    MANIFESTS["cycle-b"] = SubstrateManifest(name="cycle-b", depends_on=["cycle-a"])
    try:
        with pytest.raises(SubstrateResolutionError, match="cycle"):
            _topological_sort({"cycle-a", "cycle-b"})
    finally:
        del MANIFESTS["cycle-a"]
        del MANIFESTS["cycle-b"]


def test_topological_sort_detects_self_cycle() -> None:
    from vcs_core.manifest import MANIFESTS, SubstrateManifest

    MANIFESTS["self-dep"] = SubstrateManifest(name="self-dep", depends_on=["self-dep"])
    try:
        with pytest.raises(SubstrateResolutionError, match="cycle"):
            _topological_sort({"self-dep"})
    finally:
        del MANIFESTS["self-dep"]
