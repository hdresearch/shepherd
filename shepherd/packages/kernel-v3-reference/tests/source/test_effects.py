import pytest

from shepherd_kernel_v3_reference.schemas import TaggedRecordSchema, TypeSchema
from shepherd_kernel_v3_reference.source.effects import EffectRegistry, EffectSignature


def make_sig(kind: str = "eff.foo") -> EffectSignature:
    return EffectSignature(
        effect_kind=kind,
        payload_schema=TypeSchema(dict),
        operation_result_schema=TaggedRecordSchema("Result"),
    )


def test_register_then_lookup() -> None:
    r = EffectRegistry()
    sig = make_sig()
    r.register(sig)
    assert r.lookup("eff.foo") is sig


def test_membership_check() -> None:
    r = EffectRegistry()
    r.register(make_sig("eff.bar"))
    assert "eff.bar" in r
    assert "eff.foo" not in r


def test_duplicate_registration_rejected() -> None:
    r = EffectRegistry()
    r.register(make_sig())
    with pytest.raises(ValueError, match="already registered"):
        r.register(make_sig())


def test_unknown_lookup_raises() -> None:
    r = EffectRegistry()
    with pytest.raises(KeyError, match="unknown effect"):
        r.lookup("eff.never-registered")
