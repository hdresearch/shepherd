from __future__ import annotations

import json
from dataclasses import dataclass

import pytest
from shepherd_runtime.nucleus import GitRepo, GitRepoBasis

from shepherd_dialect.workspace_control.errors import WorkspaceControlError
from shepherd_dialect.workspace_control.gitrepo_handles import (
    readonly_git_repo_for_retained_output,
    require_selected_workspace_git_repo,
    retained_output_git_repo_basis,
    same_git_binding_state,
    selected_workspace_git_repo,
)


@dataclass(frozen=True)
class _SelectedBindingRevision:
    binding: str = "workspace"
    store_id: str = "store-workspace"
    resource_id: str = "workspace"
    head: str = "head-1"


class _FakeVcsCore:
    _DEFAULT_SELECTED = object()

    def __init__(
        self,
        *,
        world_oid: str | None = "world-1",
        selected: _SelectedBindingRevision | None | object = _DEFAULT_SELECTED,
    ) -> None:
        self._world_oid = world_oid
        self._selected = _SelectedBindingRevision() if selected is self._DEFAULT_SELECTED else selected
        self.binding_reads: list[str] = []

    def world_oid(self) -> str | None:
        return self._world_oid

    def read_selected_binding_revision_with_head(self, binding: str) -> _SelectedBindingRevision | None:
        self.binding_reads.append(binding)
        return self._selected  # type: ignore[return-value]


@dataclass(frozen=True)
class _OutputIdentity:
    candidate_head: str = "candidate-head-1"


@dataclass(frozen=True)
class _RetainedOutput:
    output_world_oid: str = "output-world-1"
    store_id: str = "store-workspace"
    resource_id: str = "workspace"
    identity: _OutputIdentity = _OutputIdentity()
    binding: str = "workspace"


def test_selected_workspace_git_repo_hydrates_current_binding_basis() -> None:
    mg = _FakeVcsCore()

    repo = selected_workspace_git_repo(mg)

    assert repo == GitRepo(
        binding="workspace",
        basis=GitRepoBasis(
            world_oid="world-1",
            store_id="store-workspace",
            resource_id="workspace",
            head="head-1",
        ),
        authority=frozenset({"read", "write"}),
    )
    assert mg.binding_reads == ["workspace"]


def test_require_selected_workspace_git_repo_uses_binding_state_not_world_provenance() -> None:
    mg = _FakeVcsCore(world_oid="world-after-ledger-drift")
    current = selected_workspace_git_repo(mg)
    copied = GitRepo.from_payload(json.loads(json.dumps(current.to_payload())))
    same_state_different_world = GitRepo(
        binding="workspace",
        basis=GitRepoBasis(
            world_oid="synthetic-world-provenance",
            store_id=current.basis.store_id,
            resource_id=current.basis.resource_id,
            head=current.basis.head,
        ),
        authority=current.authority,
    )

    assert require_selected_workspace_git_repo(mg, copied) == copied
    assert require_selected_workspace_git_repo(mg, same_state_different_world) == same_state_different_world
    assert same_git_binding_state(current.basis, same_state_different_world.basis)
    assert current.basis != same_state_different_world.basis


@pytest.mark.parametrize(
    ("repo", "message"),
    [
        ("not-a-repo", "requires a GitRepo"),
        (
            GitRepo(
                binding="other",
                basis=GitRepoBasis(
                    world_oid="world-1",
                    store_id="store-workspace",
                    resource_id="workspace",
                    head="head-1",
                ),
                authority=frozenset({"read", "write"}),
            ),
            "workspace GitRepo binding",
        ),
        (
            GitRepo(
                binding="workspace",
                basis=GitRepoBasis(
                    world_oid="world-1",
                    store_id="store-workspace",
                    resource_id="workspace",
                    head="head-1",
                ),
                authority=frozenset({"read"}),
            ),
            "read/write",
        ),
        (
            GitRepo(
                binding="workspace",
                basis=GitRepoBasis(
                    world_oid="world-1",
                    store_id="store-workspace",
                    resource_id="workspace",
                    head="stale-head",
                ),
                authority=frozenset({"read", "write"}),
            ),
            "current selected workspace binding state",
        ),
    ],
)
def test_require_selected_workspace_git_repo_rejects_invalid_values(repo: object, message: str) -> None:
    mg = _FakeVcsCore()

    with pytest.raises(WorkspaceControlError, match=message):
        require_selected_workspace_git_repo(mg, repo)


@pytest.mark.parametrize(
    ("mg", "message"),
    [
        (_FakeVcsCore(world_oid=None), "current workspace world"),
        (_FakeVcsCore(selected=None), "selected workspace binding"),
    ],
)
def test_selected_workspace_git_repo_fails_closed_without_selected_state(
    mg: _FakeVcsCore,
    message: str,
) -> None:
    with pytest.raises(WorkspaceControlError, match=message):
        selected_workspace_git_repo(mg)


def test_retained_output_hydrates_readonly_git_repo_value() -> None:
    output = _RetainedOutput()

    basis = retained_output_git_repo_basis(output)
    repo = readonly_git_repo_for_retained_output(output)

    assert basis == GitRepoBasis(
        world_oid="output-world-1",
        store_id="store-workspace",
        resource_id="workspace",
        head="candidate-head-1",
    )
    assert repo == GitRepo(binding="workspace", basis=basis, authority=frozenset({"read"}))
