"""Private relationship validation for v2 world snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pygit2

from vcs_core._world_types import StructuredIssue, WorldSnapshot

if TYPE_CHECKING:
    from collections.abc import Mapping

    from vcs_core._substrate_store import SubstrateStore
    from vcs_core._transition_kernel_records import RelationshipRequirement


@dataclass(frozen=True)
class RelationshipValidationReport:
    """Validation result for cross-binding relationship requirements."""

    issues: tuple[StructuredIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues


def validate_relationships(
    snapshot: WorldSnapshot,
    stores: Mapping[str, SubstrateStore],
    requirements: tuple[RelationshipRequirement, ...],
) -> RelationshipValidationReport:
    """Validate first-cut exact and descends-from requirements against selected heads."""
    issues: list[StructuredIssue] = []
    heads = snapshot.by_binding()
    for requirement in requirements:
        source = heads.get(requirement.binding)
        if source is None:
            issues.append(_issue("relationship_missing_source", requirement, "relationship source binding is absent"))
            continue
        selected = heads.get(requirement.target_binding)
        if selected is None:
            issues.append(_issue("relationship_missing_target", requirement, "relationship target binding is absent"))
            continue
        store = stores.get(selected.store_id)
        if store is None:
            issues.append(_issue("relationship_missing_store", requirement, "relationship target store is absent"))
            continue
        if requirement.relation == "exact":
            if selected.head != requirement.target_head:
                issues.append(
                    _issue("relationship_exact_mismatch", requirement, "selected head does not match required head")
                )
        elif requirement.relation == "descends-from":
            descends_issue = _descends_from_issue(store.repo, selected.head, requirement.target_head)
            if descends_issue is not None:
                issues.append(
                    _issue(
                        descends_issue,
                        requirement,
                        _DESCENDS_FROM_MESSAGES[descends_issue],
                    )
                )
        else:
            issues.append(_issue("relationship_unknown_relation", requirement, "unsupported relationship relation"))
    return RelationshipValidationReport(tuple(issues))


_DESCENDS_FROM_MESSAGES = {
    "relationship_malformed_head": "relationship selected or required head is malformed",
    "relationship_missing_head": "relationship selected or required head is absent from the target store",
    "relationship_descends_from_mismatch": "selected head does not descend from required head",
}


def _descends_from_issue(repo: pygit2.Repository, selected_head: str, required_head: str) -> str | None:
    if selected_head == required_head:
        return None
    try:
        selected = pygit2.Oid(hex=selected_head)
        required = pygit2.Oid(hex=required_head)
    except ValueError:
        return "relationship_malformed_head"
    if not isinstance(repo.get(selected), pygit2.Commit) or not isinstance(repo.get(required), pygit2.Commit):
        return "relationship_missing_head"
    if not repo.descendant_of(selected, required):
        return "relationship_descends_from_mismatch"
    return None


def _issue(code: str, requirement: RelationshipRequirement, message: str) -> StructuredIssue:
    return StructuredIssue(
        code=code,
        message=message,
        binding=requirement.binding,
        ref=requirement.target_binding,
        recovery_hint=f"Select a {requirement.target_binding!r} head satisfying {requirement.relation!r}.",
    )
