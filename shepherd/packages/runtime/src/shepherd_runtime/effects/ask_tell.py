"""``ask`` and ``tell`` perform-side functions for Plan 04 effects.

CONTRACTS C1 perform side + DECISIONS D7.

Per-class ``on_unhandled`` policy is enforced at perform-time:

- ``Tell`` default ``on_unhandled="ignore"`` → return ``None``
- Tell with ``on_unhandled="raise"`` → raise ``UnhandledTell``
- Tell with ``on_unhandled="suspend"`` -> would suspend in production
  (no driver registry exists in this tranche; raises
  ``UnhandledTell``)
- ``Ask`` with no active handler raises ``UnhandledAsk``.

The shape-detection helper ``HandlerSignatureError`` is also
re-exported here for the public ``shepherd_runtime.effects`` surface.
"""

from __future__ import annotations

from typing import Any, TypeVar

from shepherd_runtime.effects._handler_stack import invoke_handler, resolve_handler
from shepherd_runtime.effects.base import Ask, Tell
from shepherd_runtime.effects.effect_kind import effect_key_for_event
from shepherd_runtime.effects.shape_detection import HandlerSignatureError

__all__ = [
    "HandlerSignatureError",
    "UnhandledAsk",
    "UnhandledEffect",
    "UnhandledTell",
    "ask",
    "sync_ask",
    "sync_tell",
    "tell",
]


R = TypeVar("R")


class UnhandledEffect(RuntimeError):  # noqa: N818
    """Shared parent for unhandled effect errors."""

    def __init__(self, message: str, *, effect: object) -> None:
        super().__init__(message)
        self.effect = effect
        self.kind = _effect_key(effect, "ask" if isinstance(effect, Ask) else "tell")


class UnhandledAsk(UnhandledEffect):
    """An ``Ask`` effect was performed with no handler installed.

    Carries the offending effect-class name for diagnostics. The
    durable runtime will escalate first to the workspace driver registry; the
    Phase 1 pure-response subset raises immediately because that registry does
    not exist yet.
    """

    def __init__(self, effect: object) -> None:
        kind = type(effect).__name__
        super().__init__(
            f"no handler installed for ask {kind}; "
            f"install one with `with handle({kind}, ...):` or set "
            f"`on_unhandled='raise'` to surface this at the perform site",
            effect=effect,
        )


class UnhandledTell(UnhandledEffect):
    """A ``Tell`` effect was performed with no handler installed.

    Raised only when the effect class requests ``on_unhandled='raise'``.
    """

    def __init__(self, effect: object) -> None:
        kind = type(effect).__name__
        super().__init__(
            f"no handler installed for tell {kind} under on_unhandled='raise'",
            effect=effect,
        )


async def ask(effect: Ask[R]) -> R:
    """Perform an ``Ask`` effect and await its response.

    The nearest dynamically installed handler wins. This tranche
    supports pure-response handlers only; supervisor/resumption
    handlers are reserved for the later continuation runtime.
    """
    if not isinstance(effect, Ask):
        raise TypeError(f"ask() expected an Ask subclass instance; got {type(effect).__name__}")
    binding = resolve_handler(effect)
    if binding is None:
        raise UnhandledAsk(effect)

    recorder = _active_trace_recorder()
    selection_ref: str | None = None
    if recorder is not None:
        declaration_ref = recorder.record_effect_requested(
            _effect_key(effect, "ask"),
            payload_summary=_effect_payload_summary(effect),
        )
        selection_ref = recorder.record_handler_selected(declaration_ref, handler_key=binding.handler_id)
    try:
        result = await invoke_handler(binding, effect)
    except Exception as exc:
        if recorder is not None and selection_ref is not None:
            recorder.record_effect_completed(
                selection_ref,
                status="raised",
                result_summary={"exception_type": type(exc).__name__},
            )
        raise
    if recorder is not None and selection_ref is not None:
        recorder.record_effect_completed(selection_ref, status="returned", result_summary=_result_summary(result))
    return result


async def tell(effect: Tell) -> None:
    """Perform a ``Tell`` effect (no return value).

    If a matching handler is active it is invoked and its return value
    is discarded. Without a handler, the ``Tell`` default-ignore policy
    is preserved.
    """
    if not isinstance(effect, Tell):
        raise TypeError(f"tell() expected a Tell subclass instance; got {type(effect).__name__}")
    binding = resolve_handler(effect)
    recorder = _active_trace_recorder()
    effect_key = _effect_key(effect, "tell")
    if binding is not None:
        selection_ref: str | None = None
        if recorder is not None:
            declaration_ref = recorder.record_effect_requested(
                effect_key,
                payload_summary=_effect_payload_summary(effect),
            )
            selection_ref = recorder.record_handler_selected(declaration_ref, handler_key=binding.handler_id)
        try:
            result = await invoke_handler(binding, effect)
        except Exception as exc:
            if recorder is not None and selection_ref is not None:
                recorder.record_effect_completed(
                    selection_ref,
                    status="raised",
                    result_summary={"exception_type": type(exc).__name__},
                )
            raise
        if recorder is not None and selection_ref is not None:
            recorder.record_effect_completed(selection_ref, status="returned", result_summary=_result_summary(result))
        return

    if type(effect).on_unhandled == "ignore":
        if recorder is not None:
            declaration_ref = recorder.record_effect_requested(
                effect_key,
                payload_summary=_effect_payload_summary(effect),
            )
            recorder.record_effect_default_ignored(declaration_ref)
        return
    raise UnhandledTell(effect)


def sync_ask(effect: Ask[R]) -> R:
    """Perform an ``Ask`` effect from synchronous user code."""
    from shepherd_runtime.sync import run_sync

    return run_sync(ask(effect))


def sync_tell(effect: Tell) -> None:
    """Perform a ``Tell`` effect from synchronous user code."""
    from shepherd_runtime.sync import run_sync

    return run_sync(tell(effect))


def _active_trace_recorder() -> Any | None:
    try:
        from shepherd_runtime.trace.runtime import active_trace_recorder
    except ModuleNotFoundError as exc:
        if exc.name == "shepherd_kernel_v3_reference":
            return None
        raise
    return active_trace_recorder()


def _effect_key(effect: object, prefix: str) -> str:
    del prefix
    return effect_key_for_event(effect)


def _effect_payload_summary(effect: object) -> dict[str, object]:
    fields = getattr(type(effect), "__dataclass_fields__", {})
    return {
        "effect_class": f"{type(effect).__module__}.{type(effect).__qualname__}",
        "field_names": sorted(fields),
    }


def _result_summary(result: object) -> dict[str, object]:
    if result is None:
        return {"result_type": "NoneType"}
    return {"result_type": type(result).__name__}
