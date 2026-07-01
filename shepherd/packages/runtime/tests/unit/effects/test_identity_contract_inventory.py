"""Strict-xfail inventory for the W3.B-iii-r effect identity repair."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from shepherd_runtime.effects import Ask, ConflictingKind, Match, Plan, Subset, Tell
from shepherd_runtime.nucleus import task


def test_keyword_kind_declares_public_identity() -> None:
    from shepherd_runtime.effects.effect_kind import effect_key_for_class, is_explicit_effect_kind

    class ReviewVerdict(Ask[str], kind="r0_keyword.review_verdict"):
        pass

    assert ReviewVerdict.kind == "r0_keyword.review_verdict"
    assert effect_key_for_class(ReviewVerdict) == "r0_keyword.review_verdict"
    assert is_explicit_effect_kind(ReviewVerdict)


def test_class_attribute_kind_declares_public_identity() -> None:
    from shepherd_runtime.effects.effect_kind import effect_key_for_class, is_explicit_effect_kind

    class Audit(Tell):
        kind = "r0_attr.audit"

    assert effect_key_for_class(Audit) == "r0_attr.audit"
    assert is_explicit_effect_kind(Audit)


def test_disagreeing_keyword_and_attribute_kind_rejects() -> None:
    with pytest.raises(TypeError, match="conflicting kind"):

        class Conflicting(Tell, kind="r0_conflict.left"):
            kind = "r0_conflict.right"


def test_duplicate_explicit_stable_kind_rejects_unrelated_owner() -> None:
    class First(Tell, kind="r0_duplicate.audit"):
        pass

    with pytest.raises(TypeError, match="already claimed"):

        class Second(Tell, kind="r0_duplicate.audit"):
            pass

    assert First.kind == "r0_duplicate.audit"


def test_duplicate_unannotated_local_class_names_get_distinct_local_keys() -> None:
    from shepherd_runtime.effects.effect_kind import effect_key_for_class

    def left() -> type[Tell]:
        class Audit(Tell):
            pass

        return Audit

    def right() -> type[Tell]:
        class Audit(Tell):
            pass

        return Audit

    left_key = effect_key_for_class(left())
    right_key = effect_key_for_class(right())

    assert left_key.startswith("local.")
    assert right_key.startswith("local.")
    assert left_key != right_key


def test_public_hierarchical_kind_validation_does_not_weaken_tool_model_validator() -> None:
    from shepherd_runtime.effects.effect_kind import (
        parse_matcher_kind_sugar,
        split_effect_kind,
        validate_public_effect_kind,
    )

    assert validate_public_effect_kind("filesystem") == "filesystem"
    assert validate_public_effect_kind("filesystem.write") == "filesystem.write"
    assert parse_matcher_kind_sugar("filesystem.**") == ("subtree", "filesystem")
    assert parse_matcher_kind_sugar("filesystem.*") == ("descendants", "filesystem")

    with pytest.raises(ValueError, match="reserved 'local'"):
        validate_public_effect_kind("local.example")
    with pytest.raises(ValueError):
        split_effect_kind("tool.fs.read_file")


def test_tell_category_root_is_not_public_tell_namespace() -> None:
    @dataclass(frozen=True)
    class FilesystemWrite(Tell, kind="r0_filesystem.write"):
        path: str

    event = FilesystemWrite(path="README.md")

    assert Match.subtree(Tell).matches(event)
    assert not Match.subtree("tell").matches(event)


def test_class_and_string_matchers_are_related_but_not_broadly_interchangeable() -> None:
    @dataclass(frozen=True)
    class Audit(Tell, kind="r0_equivalence.audit"):
        message: str

    assert Match.exact(Audit).equivalent_to(Match.exact("r0_equivalence.audit")) is Subset.Yes
    assert Match.subtree(Audit).subset_of(Match.subtree("r0_equivalence.audit")) is Subset.Unknown
    assert Match.subtree("r0_equivalence.audit").subset_of(Match.subtree(Audit)) is Subset.Unknown


def test_bare_kind_attribute_is_not_public_event_identity() -> None:
    from shepherd_runtime.effects.effect_kind import effect_key_for_class

    @dataclass(frozen=True)
    class Audit(Tell, kind="r0_kind_boundary.audit"):
        message: str = ""

    class FakeKindOnly:
        kind = "r0_kind_boundary.audit"

    assert effect_key_for_class(FakeKindOnly).startswith("local.")
    assert not Match.exact("r0_kind_boundary.audit").matches(FakeKindOnly())
    assert Match.exact("r0_kind_boundary.audit").matches(Audit())
    assert Match.exact("r0_kind_boundary.audit").subset_of(Match.exact(Audit)) is Subset.Yes


def test_raw_string_is_matcher_key_not_runtime_event() -> None:
    @dataclass(frozen=True)
    class Audit(Tell, kind="r0_raw_string.audit"):
        message: str = ""

    assert not Match.exact("r0_raw_string.audit").matches("r0_raw_string.audit")
    assert not Match.exact(Audit).matches("r0_raw_string.audit")
    assert Match.exact("r0_raw_string.audit").equivalent_to(Match.exact(Audit)) is Subset.Yes


def test_framework_surface_events_register_public_identity() -> None:
    from shepherd_runtime.trace.runtime import EffectRequested

    event = EffectRequested(ref="surface:effect-requested", timestamp_us=0)

    assert Match.exact("effect_requested").matches(event)
    assert Match.exact("effect_requested").equivalent_to(Match.exact(EffectRequested)) is Subset.Yes


def test_public_kind_child_must_declare_descendant_kind() -> None:
    @dataclass(frozen=True)
    class FilesystemEffect(Tell, kind="r0_hierarchy.filesystem"):
        path: str

    @dataclass(frozen=True)
    class FilesystemWrite(FilesystemEffect, kind="r0_hierarchy.filesystem.write"):
        content: bytes

    event = FilesystemWrite(path="/tmp/x", content=b"y")

    assert Match.subtree(FilesystemEffect).matches(event)
    assert Match.subtree("r0_hierarchy.filesystem").matches(event)

    with pytest.raises(ConflictingKind, match="must declare an explicit descendant"):

        class FilesystemRead(FilesystemEffect):
            pass

    with pytest.raises(ConflictingKind, match="must be a descendant"):

        class NetworkWrite(FilesystemEffect, kind="r0_hierarchy.network.write"):
            pass


def test_multiple_unrelated_public_kind_parents_reject() -> None:
    class Left(Tell, kind="r0_multi.left"):
        pass

    class Right(Tell, kind="r0_multi.right"):
        pass

    with pytest.raises(ConflictingKind, match="multiple public kind-bearing parents"):

        class Both(Left, Right, kind="r0_multi.both"):
            pass


def test_failed_class_creation_does_not_claim_explicit_kind() -> None:
    with pytest.raises(TypeError, match="Ask subclass cannot use on_unhandled='ignore'"):

        class Bad(Ask[str], kind="r0_poison.effect", on_unhandled="ignore"):
            pass

    class Good(Tell, kind="r0_poison.effect"):
        pass

    assert Good.kind == "r0_poison.effect"


def test_structural_task_may_is_metadata_not_coarse_profile() -> None:
    @task(may=Plan().allow_only("r0_task.**"))
    def sample() -> str:
        return "ok"

    assert sample.metadata.may is None
    assert sample.metadata.structural_may is not None
    assert sample.metadata.structural_may.match == Match.subtree("r0_task")
