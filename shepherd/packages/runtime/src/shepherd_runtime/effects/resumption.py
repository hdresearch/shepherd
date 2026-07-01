"""Resumption Protocol for supervisor-shape handler bodies.

CONTRACTS B3 + DECISIONS D6, D14, D16.

Supervisor handlers receive an effect payload plus a ``Resumption``
callable. They invoke ``resume(operation_result)`` explicitly,
observe the worker's tail value, perform additional effects, and
return the handler answer. Branch-affine: one resume call per
``Resumption`` per branch identity.

Per D14, supervisor handlers are ``async def`` only. Pure-response
handlers may still be sync (per D6). The framework rejects sync
second-parameter-``Resumption`` shapes at handler registration with
``HandlerSignatureError``.

Per D16, a ``Resumption`` is consumed at the *start* of the call,
not at completion; cancellation does not un-consume it. A second
resume after a cancelled first call still raises
``ResumptionConsumed``. ``ResumptionAborted`` (structured control)
and ``CancelledError`` (exceptional) are distinct.
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar

__all__ = ["Resumption", "ResumptionAborted", "ResumptionConsumed"]


T_in_contra = TypeVar("T_in_contra", contravariant=True)
T_out_co = TypeVar("T_out_co", covariant=True)


class Resumption(Protocol, Generic[T_in_contra, T_out_co]):
    """Callable handle for a captured worker continuation.

    Used as the second-parameter annotation on supervisor-shape
    handler bodies. Variance follows standard callable variance:
    ``Resumption[Wide, Narrow] :> Resumption[Narrow, Wide]``.
    """

    async def __call__(self, value: T_in_contra, /) -> T_out_co: ...


class ResumptionConsumed(RuntimeError):  # noqa: N818
    """A branch-affine ``Resumption`` was called more than once.

    Raised by the framework at the second ``await resume(...)`` call
    in the same branch identity, regardless of whether the first call
    completed (per DECISIONS D16: consumption is at call start, not
    completion).
    """


class ResumptionAborted(RuntimeError):  # noqa: N818
    """An outer handler aborted the worker continuation.

    The abort happens while a supervisor's ``await resume(...)`` was in flight.

    Distinct from ``asyncio.CancelledError``: this is *structured
    control* (the outer handler made a decision), not an exceptional
    cancellation (the run got torn down). Supervisor authors catch
    this at the resume call site; the catch block runs in the
    supervisor's own region.
    """
