"""Contract tests for the pure GitRepo value noun."""

from __future__ import annotations

import importlib
import inspect

import pytest

TARGET_MODULE = "shepherd_runtime.nucleus.handles"


def _git_repo_type() -> type:
    module = importlib.import_module(TARGET_MODULE)
    return module.GitRepo


def _git_repo_basis_type() -> type:
    module = importlib.import_module(TARGET_MODULE)
    return module.GitRepoBasis


def _basis(
    *,
    world_oid: str = "world:abc",
    store_id: str = "store:git",
    resource_id: str = "resource:workspace",
    head: str = "head:abc",
) -> object:
    git_repo_basis = _git_repo_basis_type()
    return git_repo_basis(world_oid=world_oid, store_id=store_id, resource_id=resource_id, head=head)


def test_git_repo_home_is_runtime_nucleus_not_shepherd2_skeleton() -> None:
    """The pure value noun lives in the runtime nucleus, not the skeleton bridge."""
    git_repo = _git_repo_type()
    git_repo_basis = _git_repo_basis_type()
    from shepherd_runtime.nucleus import GitRepo as exported
    from shepherd_runtime.nucleus import GitRepoBasis as exported_basis

    assert git_repo.__module__ == "shepherd_runtime.nucleus.handles"
    assert git_repo.__name__ == "GitRepo"
    assert git_repo_basis.__module__ == "shepherd_runtime.nucleus.handles"
    assert exported is git_repo
    assert exported_basis is git_repo_basis


def test_git_repo_module_has_no_runtime_custody_or_bridge_imports() -> None:
    """The value noun must not be an adapter around custody or skeleton bridge machinery."""
    module = importlib.import_module(TARGET_MODULE)
    source = inspect.getsource(module)

    assert "shepherd_dialect" not in source
    assert "shepherd2.vnext" not in source
    assert "vcs_core" not in source
    assert "skeleton" not in source


def test_git_repo_identity_and_equality_contract() -> None:
    """Binding + basis + authority define pure value equality."""
    git_repo = _git_repo_type()

    basis = _basis()
    repo = git_repo(binding="workspace", basis=basis, authority=frozenset({"read", "write"}))
    same = git_repo(binding="workspace", basis=_basis(), authority=["write", "read"])
    different_basis = git_repo(
        binding="workspace",
        basis=_basis(world_oid="world:def"),
        authority=frozenset({"read", "write"}),
    )

    assert repo == same
    assert repo != different_basis
    assert repo.binding == "workspace"
    assert repo.basis == basis
    assert repo.basis.world_oid == "world:abc"
    assert repo.basis.store_id == "store:git"
    assert repo.basis.resource_id == "resource:workspace"
    assert repo.basis.head == "head:abc"
    assert repo.authority == frozenset({"read", "write"})


def test_git_repo_readonly_attenuation_is_monotone_and_idempotent() -> None:
    """Readonly attenuation never amplifies authority and collapses repeated attenuation."""
    git_repo = _git_repo_type()
    repo = git_repo(binding="workspace", basis=_basis(), authority=frozenset({"read", "write"}))

    readonly = repo.readonly()

    assert readonly.authority == frozenset({"read"})
    assert readonly.readonly() == readonly
    assert readonly.authority < repo.authority


def test_git_repo_allow_and_deny_are_monotone() -> None:
    """Explicit attenuation methods only remove authority atoms."""
    git_repo = _git_repo_type()
    repo = git_repo(binding="workspace", basis=_basis(), authority=frozenset({"read", "write"}))

    assert repo.allow_only({"write"}).authority == frozenset({"write"})
    assert repo.deny({"write"}).authority == frozenset({"read"})
    assert repo.deny({"read", "write"}).authority == frozenset()


def test_git_repo_serde_roundtrip_preserves_authority_without_amplification() -> None:
    """Cross-boundary representation round-trips without gaining authority."""
    git_repo = _git_repo_type()
    repo = git_repo(binding="workspace", basis=_basis(), authority=frozenset({"read"}))

    payload = repo.to_payload()
    round_tripped = git_repo.from_payload(payload)

    assert payload["basis"] == repo.basis.to_payload()
    assert round_tripped == repo
    assert round_tripped.basis == repo.basis
    assert round_tripped.authority == frozenset({"read"})
    assert "write" not in round_tripped.authority


def test_git_repo_rejects_invalid_identity_or_authority() -> None:
    git_repo = _git_repo_type()
    git_repo_basis = _git_repo_basis_type()

    for kwargs in (
        {"binding": "", "basis": _basis(), "authority": frozenset({"read"})},
        {"binding": "workspace", "basis": "world:abc", "authority": frozenset({"read"})},
    ):
        with pytest.raises((TypeError, ValueError)):
            git_repo(**kwargs)

    for authority in ("read", frozenset({"admin"}), frozenset({object()})):
        with pytest.raises((TypeError, ValueError)):
            git_repo(binding="workspace", basis=_basis(), authority=authority)

    for kwargs in (
        {"world_oid": "", "store_id": "store:git", "resource_id": "resource:workspace", "head": "head:abc"},
        {"world_oid": "world:abc", "store_id": "", "resource_id": "resource:workspace", "head": "head:abc"},
        {"world_oid": "world:abc", "store_id": "store:git", "resource_id": "", "head": "head:abc"},
        {"world_oid": "world:abc", "store_id": "store:git", "resource_id": "resource:workspace", "head": ""},
    ):
        with pytest.raises(ValueError):
            git_repo_basis(**kwargs)


def test_git_repo_payload_validation_fails_closed() -> None:
    git_repo = _git_repo_type()
    basis_payload = _basis().to_payload()

    invalid_payloads = (
        {},
        {"schema": "other", "binding": "workspace", "basis": basis_payload, "authority": ["read"]},
        {
            "schema": "shepherd.runtime.nucleus.gitrepo.v1",
            "binding": 1,
            "basis": basis_payload,
            "authority": ["read"],
        },
        {"schema": "shepherd.runtime.nucleus.gitrepo.v1", "binding": "workspace", "basis": 1, "authority": ["read"]},
        {
            "schema": "shepherd.runtime.nucleus.gitrepo.v1",
            "binding": "workspace",
            "basis": {"schema": "other"},
            "authority": ["read"],
        },
        {
            "schema": "shepherd.runtime.nucleus.gitrepo.v1",
            "binding": "workspace",
            "basis": {**basis_payload, "world_oid": ""},
            "authority": ["read"],
        },
        {
            "schema": "shepherd.runtime.nucleus.gitrepo.v1",
            "binding": "workspace",
            "basis": basis_payload,
            "authority": "read",
        },
        {
            "schema": "shepherd.runtime.nucleus.gitrepo.v1",
            "binding": "workspace",
            "basis": basis_payload,
            "authority": ["write", "admin"],
        },
    )

    for payload in invalid_payloads:
        with pytest.raises((TypeError, ValueError)):
            git_repo.from_payload(payload)
