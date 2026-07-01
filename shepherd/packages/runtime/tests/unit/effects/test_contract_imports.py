"""Contract-import tests for Plan 04 effects-nucleus stubs.

Satisfies CONTRACTS Maintenance Rule 3 for B1, B2, B3, B5, C1, C2, C6
by importing each contract from the production module path and
exercising the documented surface against a stub-level
implementation.

The runtime layer is a stub: ``handle()`` installs nothing,
``ask()`` raises ``UnhandledAsk``, ``tell()`` honors the default
ignore policy. Production handler dispatch is Tranche 7+ work — these
tests pin the *importable contract surface*, not runtime correctness.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from typing import ClassVar, Literal

import pytest

# ---------------------------------------------------------------------------
# B1, B2: Ask / Tell base classes + _EffectMeta
# ---------------------------------------------------------------------------


def test_ask_tell_imports_from_runtime_effects() -> None:
    from shepherd_runtime.effects import Ask, Tell, _EffectMeta

    assert Ask.__name__ == "Ask"
    assert Tell.__name__ == "Tell"
    assert _EffectMeta.__name__ == "_EffectMeta"


def test_ask_subclass_with_keyword_form_sets_on_unhandled() -> None:
    from shepherd_runtime.effects import Ask

    @dataclass(frozen=True)
    class AmbiguousDesign(Ask[str], on_unhandled="raise"):
        options: tuple[str, ...]

    assert AmbiguousDesign.on_unhandled == "raise"


def test_ask_class_attribute_form_sets_on_unhandled() -> None:
    from shepherd_runtime.effects import Ask

    @dataclass(frozen=True)
    class CriticalChoice(Ask[str]):
        on_unhandled: ClassVar[Literal["raise", "suspend"]] = "raise"
        options: tuple[str, ...]

    assert CriticalChoice.on_unhandled == "raise"


def test_ask_rejects_ignore_at_class_creation() -> None:
    from shepherd_runtime.effects import Ask

    with pytest.raises(TypeError, match="cannot use on_unhandled='ignore'"):

        class Bad(Ask[str], on_unhandled="ignore"):
            pass


def test_tell_default_is_ignore() -> None:
    from shepherd_runtime.effects import Tell

    @dataclass(frozen=True)
    class RiskFound(Tell):
        severity: int

    assert RiskFound.on_unhandled == "ignore"


def test_tell_accepts_all_three_values() -> None:
    from shepherd_runtime.effects import Tell

    for value in ("raise", "suspend", "ignore"):

        @dataclass(frozen=True)
        class _T(Tell, on_unhandled=value):
            pass

        assert _T.on_unhandled == value


def test_invalid_on_unhandled_value_rejected() -> None:
    from shepherd_runtime.effects import Tell

    with pytest.raises(TypeError, match="invalid on_unhandled"):

        class Bad(Tell, on_unhandled="bogus"):
            pass


def test_frozen_dataclass_subclass_is_immutable_and_hashable() -> None:
    from shepherd_runtime.effects import Ask

    @dataclass(frozen=True)
    class Choice(Ask[str], on_unhandled="raise"):
        options: tuple[str, ...]

    a = Choice(options=("x", "y"))
    b = Choice(options=("x", "y"))
    assert a == b
    assert hash(a) == hash(b)
    with pytest.raises(FrozenInstanceError):
        a.options = ("z",)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# B3: Resumption Protocol
# ---------------------------------------------------------------------------


def test_resumption_imports_from_runtime_effects() -> None:
    from shepherd_runtime.effects import (
        Resumption,
        ResumptionAborted,
        ResumptionConsumed,
    )

    assert Resumption.__name__ == "Resumption"
    assert issubclass(ResumptionConsumed, RuntimeError)
    assert issubclass(ResumptionAborted, RuntimeError)


def test_resumption_protocol_is_callable_protocol() -> None:
    """A concrete async-callable class satisfies the Protocol structurally."""
    from shepherd_runtime.effects import Resumption

    class _Stub:
        async def __call__(self, value: int, /) -> str:  # pragma: no cover - protocol
            return str(value)

    # Protocol membership at the type system; runtime check via structural
    # callable shape.
    s = _Stub()
    assert callable(s)
    # Not strictly an instance check (Resumption is a Protocol); used here
    # only to verify import works.
    assert Resumption is not None


# ---------------------------------------------------------------------------
# B5: Effect-kind naming
# ---------------------------------------------------------------------------


def test_split_effect_kind_round_trips() -> None:
    from shepherd_runtime.effects import split_effect_kind

    assert split_effect_kind("tool.read_file") == ("tool", "read_file")
    assert split_effect_kind("model.call") == ("model", "call")


def test_split_effect_kind_rejects_multiple_dots() -> None:
    from shepherd_runtime.effects import split_effect_kind

    with pytest.raises(ValueError, match=r"namespace\.name"):
        split_effect_kind("tool.fs.read_file")


def test_tool_kind_validates_name() -> None:
    from shepherd_runtime.effects import tool_kind

    assert tool_kind("read_file") == "tool.read_file"
    with pytest.raises(ValueError):
        tool_kind("BadName")  # uppercase rejected
    with pytest.raises(ValueError):
        tool_kind("3leading_digit")


def test_model_kind_validates_and_blocks_reserved() -> None:
    from shepherd_runtime.effects import model_kind

    assert model_kind("call") == "model.call"
    with pytest.raises(ValueError, match="reserved"):
        model_kind("embed")
    with pytest.raises(ValueError, match="reserved"):
        model_kind("stream")


# ---------------------------------------------------------------------------
# B6: Match / Plan effect-surface values
# ---------------------------------------------------------------------------


def test_match_plan_policy_imports_from_runtime_effects() -> None:
    from shepherd_runtime.effects import Match, Plan, Subset

    assert Match.all().subset_of(Match.all()) is Subset.Yes
    assert Plan().allow_only(Match.all()).effective_surface() == Match.all()


def test_path_a_error_family_imports_from_runtime_effects() -> None:
    from shepherd_runtime.effects import (
        EffectNotPermitted,
        EffectSurfaceEmpty,
        EffectSurfaceTooWide,
        OverbroadHandler,
        PlanNotExtractable,
        UnhandledEffect,
    )

    assert issubclass(EffectNotPermitted, Exception)
    assert issubclass(EffectSurfaceEmpty, Exception)
    assert issubclass(EffectSurfaceTooWide, Exception)
    assert issubclass(OverbroadHandler, ValueError)
    assert issubclass(PlanNotExtractable, ValueError)
    assert issubclass(UnhandledEffect, RuntimeError)


# ---------------------------------------------------------------------------
# C1: handle()
# ---------------------------------------------------------------------------


def test_handle_imports_and_acts_as_sync_context_manager() -> None:
    from shepherd_runtime.effects import Ask, handle

    @dataclass(frozen=True)
    class Pick(Ask[str], on_unhandled="raise"):
        options: tuple[str, ...]

    with handle(Pick, lambda e: e.options[0]):
        pass  # no-op stub installs nothing


def test_handle_acts_as_async_context_manager() -> None:
    import asyncio

    from shepherd_runtime.effects import Tell, handle

    @dataclass(frozen=True)
    class Risk(Tell):
        severity: int

    async def _go() -> None:
        async with handle(Risk, lambda e: None):
            pass

    asyncio.run(_go())


def test_handle_dict_form_accepts_multiple_effects() -> None:
    from shepherd_runtime.effects import Ask, Tell, handle

    @dataclass(frozen=True)
    class A(Ask[str], on_unhandled="raise"):
        pass

    @dataclass(frozen=True)
    class B(Tell):
        pass

    with handle({A: lambda e: "ok", B: lambda e: None}):
        pass


def test_handle_string_kind_form_accepts_tool_kinds() -> None:
    from shepherd_runtime.effects import handle

    with handle("tool.read_file", lambda e: {"contents": "hi"}):
        pass


def test_handle_rejects_invalid_signature() -> None:
    from shepherd_runtime.effects import HandlerSignatureError, handle

    @dataclass(frozen=True)
    class _E:
        pass

    def zero_args() -> str:  # type: ignore[misc]
        return ""

    with pytest.raises(HandlerSignatureError):
        handle(_E, zero_args)


# ---------------------------------------------------------------------------
# C2: Handler body shape detection
# ---------------------------------------------------------------------------


def test_pure_response_shape_detected() -> None:
    from shepherd_runtime.effects import detect_handler_shape

    def fn(e):  # type: ignore[no-untyped-def]
        return "x"

    assert detect_handler_shape(fn) == "pure_response"


def test_supervisor_shape_detected_when_async() -> None:
    from shepherd_runtime.effects import Resumption, detect_handler_shape

    async def supervisor(e, resume: Resumption[int, str]) -> str:
        return await resume(1)

    assert detect_handler_shape(supervisor) == "supervisor"


def test_sync_supervisor_rejected_per_d14() -> None:
    from shepherd_runtime.effects import (
        HandlerSignatureError,
        Resumption,
        detect_handler_shape,
    )

    def bad(e, resume: Resumption[int, str]) -> str:  # type: ignore[no-untyped-def]
        return ""

    with pytest.raises(HandlerSignatureError, match="async def"):
        detect_handler_shape(bad)


def test_two_arg_without_resumption_annotation_rejected() -> None:
    from shepherd_runtime.effects import HandlerSignatureError, detect_handler_shape

    async def bad(e, also_e):  # type: ignore[no-untyped-def]
        return ""

    with pytest.raises(HandlerSignatureError, match="annotated"):
        detect_handler_shape(bad)


# ---------------------------------------------------------------------------
# Stub semantics: ask raises, tell ignores by default
# ---------------------------------------------------------------------------


def test_ask_stub_raises_unhandled() -> None:
    import asyncio

    from shepherd_runtime.effects import Ask, UnhandledAsk, ask

    @dataclass(frozen=True)
    class Pick(Ask[str], on_unhandled="raise"):
        pass

    async def _go() -> None:
        with pytest.raises(UnhandledAsk):
            await ask(Pick())

    asyncio.run(_go())


def test_tell_stub_default_ignore_returns_none() -> None:
    import asyncio

    from shepherd_runtime.effects import Tell, tell

    @dataclass(frozen=True)
    class Risk(Tell):
        severity: int

    async def _go() -> None:
        result = await tell(Risk(severity=1))
        assert result is None

    asyncio.run(_go())


def test_tell_with_raise_policy_raises_unhandled() -> None:
    import asyncio

    from shepherd_runtime.effects import Tell, UnhandledTell, tell

    @dataclass(frozen=True)
    class Critical(Tell, on_unhandled="raise"):
        severity: int

    async def _go() -> None:
        with pytest.raises(UnhandledTell):
            await tell(Critical(severity=10))

    asyncio.run(_go())
