from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import TYPE_CHECKING

import pytest
from commons_vcs import Edge, Failure, Object, Profile, Repo
from commons_vcs.backends import MemoryBackend

if TYPE_CHECKING:
    from commons_vcs.kernel import Resolver


def _accept(_obj: Object, _resolver: Resolver) -> Failure | None:
    return None


def _require_ok_body(obj: Object, _resolver: Resolver) -> Failure | None:
    if obj.body.get("ok") is True:
        return None
    return Failure("schema", "body.ok must be true")


def test_repo_rejects_duplicate_schema_ownership_at_construction() -> None:
    first = Profile(name="first", validators={"example/v1": _accept})
    second = Profile(name="second", validators={"example/v1": _accept})

    with pytest.raises(ValueError, match="owned by multiple profiles"):
        Repo(profiles=[first, second])


def test_repo_rejects_duplicate_schema_ownership_after_mutation() -> None:
    first = Profile(name="first", validators={"example/v1": _accept})
    second = Profile(name="second", validators={"example/v1": _accept})
    repo = Repo(profiles=[first])

    with pytest.raises(AttributeError):
        repo.profiles.append(second)  # type: ignore[attr-defined]


def test_profile_validators_are_copied_and_immutable() -> None:
    validators = {"example/v1": _accept}
    profile = Profile(name="example", validators=validators)
    validators["other/v1"] = _accept

    assert not profile.owns("other/v1")
    with pytest.raises(TypeError):
        profile.validators["other/v1"] = _accept  # type: ignore[index]


def test_profile_fields_cannot_be_reassigned() -> None:
    profile = Profile(name="example", validators={"example/v1": _accept})

    with pytest.raises(FrozenInstanceError):
        profile.name = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        profile.validators = {"other/v1": _accept}  # type: ignore[misc]


def test_repo_fields_cannot_be_reassigned() -> None:
    repo = Repo(
        profiles=[Profile(name="example", validators={"example/v1": _accept})],
        backend=MemoryBackend(),
    )

    with pytest.raises(FrozenInstanceError):
        repo.profiles = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        repo.backend = MemoryBackend()  # type: ignore[misc]


def test_verify_can_validate_trust_root_when_requested() -> None:
    backend = MemoryBackend()
    profile = Profile(name="example", validators={"example/v1": _require_ok_body})
    repo = Repo(profiles=[profile], backend=backend)
    invalid_root = Object(schema_ref="example/v1", body={"ok": False})
    root_id = backend.write_object(invalid_root)

    trusted = repo.verify(head=root_id, trust_root=root_id)
    assert trusted.outcome == "ok.verified"
    assert trusted.failures == []

    validated = repo.verify(head=root_id, trust_root=root_id, validate_trust_root=True)
    assert validated.outcome == "fail.invalid_object"
    assert [(failure.digest, failure.reason_kind) for failure in validated.failures] == [(root_id, "schema")]


def test_verify_reports_missing_trust_root_when_validation_requested() -> None:
    profile = Profile(name="example", validators={"example/v1": _require_ok_body})
    repo = Repo(profiles=[profile], backend=MemoryBackend())
    root_id = "sha256:" + "0" * 64

    result = repo.verify(head=root_id, trust_root=root_id, validate_trust_root=True)

    assert result.outcome == "unknown.incomplete"
    assert result.missing == [root_id]


def test_verify_reports_unowned_schema_as_structured_failure() -> None:
    repo = Repo(profiles=[], backend=MemoryBackend())
    obj = Object(schema_ref="external/v1", body={"ok": True})
    obj_id = repo.backend.write_object(obj)

    result = repo.verify(head=obj_id, trust_root=obj_id, validate_trust_root=True)

    assert result.outcome == "fail.invalid_object"
    assert result.failures[0].digest == obj_id
    assert result.failures[0].schema_ref == "external/v1"
    assert result.failures[0].reason_kind == "unowned_schema"


def test_repo_composes_independent_profiles_with_cross_profile_edges() -> None:
    left_profile = Profile(name="left", validators={"left/root/v1": _accept})
    right_profile = Profile(name="right", validators={"right/child/v1": _accept})
    repo = Repo(profiles=[left_profile, right_profile], backend=MemoryBackend())

    root = Object(schema_ref="left/root/v1", body={"ok": True})
    root_id = repo.append(root)
    child = Object(
        schema_ref="right/child/v1",
        body={"ok": True},
        edges=(Edge(role="right.references_left", target=root_id),),
    )
    child_id = repo.append(child)

    assert repo.get(root_id) == root
    assert repo.get(child_id) == child
    assert repo.cited_by(root_id, "right.references_left") == [child_id]
