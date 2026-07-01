"""Pattern B supervision on the dialect path — the check-at-commit cell (v1).

The run driver emits ``SubstrateOperationProposed`` for each captured
workspace change at **return time** — after the body ran, before the
coordinator's reversible wrap merges. That is the check-at-commit point of
the check-time law (an effect may be checked no later than its last undo
point; the wrap's discard IS the undo): a supervisor handler **approves by
returning** or **denies by raising** ``SupervisorDenied`` — the raise
propagates out of ``prepare_bound``, the wrap discards the run scope, the
run fails cleanly, ground pristine.

``drafts_only_supervisor`` is the spec's §7.3 worked example
(v1-integration.md): per-path gating of workspace writes. The eventual
``Match.where(...)`` predicate compilation is the spec's `Match`-algebra
work; v1 ships the literal predicate.

Scope (v1): proposals cover created/patched files from the captured delta.
Deletions are not proposed (the hook vocabulary carries FileCreate/FilePatch;
a FileDelete hook kind rides the shared-effects-contract work).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vcs_core.runtime_substrate import (
    FileCreate,
    FilePatch,
    SubstrateOperationProposed,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "SupervisorDenied",
    "drafts_only_supervisor",
    "supervisor_frame",
]


class SupervisorDenied(Exception):  # noqa: N818 — the spec's pinned name (v1-integration.md §3.4/§7.3)
    """A supervisor refused a proposed substrate operation (Pattern B denial).

    Raised from a ``SubstrateOperationProposed`` handler; the reversible wrap
    discards the run scope, so the denied work never reaches ground.
    """

    def __init__(self, *, effect: object, reason: str) -> None:
        super().__init__(reason)
        self.effect = effect
        self.reason = reason


def supervisor_frame(handler: Callable[[SubstrateOperationProposed], None]) -> Mapping[type, Callable[..., object]]:
    """One supervisor as a handler-stack frame (push via ``supervisor_handlers``)."""
    return {SubstrateOperationProposed: handler}


def drafts_only_supervisor(proposed: SubstrateOperationProposed) -> None:
    """The v1 worked example: approve workspace writes under ``drafts/`` only."""
    effect = proposed.effect
    if isinstance(effect, (FileCreate, FilePatch)) and not effect.path.startswith("drafts/"):
        raise SupervisorDenied(
            effect=effect,
            reason=f"path {effect.path!r} outside ./drafts/",
        )
