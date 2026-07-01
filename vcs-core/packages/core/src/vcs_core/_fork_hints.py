"""Coordinator-internal typed fork hints (PD2a).

The fork path used to thread a stringly ``hints`` dict from every call site
down to ``substrate.branch(...)`` — a misspelled key was silently ignored.
``ForkHints`` types the coordinator's side: a wrong key is a crash at
construction. The public ``branch(hints: dict)`` signature is unchanged
(``FilesystemSubstrate`` is baseline-pinned public); the built-in substrates
validate the dict at their boundary instead (reject-unknown-keys —
``decisions.md`` ``branch-hints-reject-unknown-keys``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from vcs_core._errors import UnknownForkHintError

if TYPE_CHECKING:
    from collections.abc import Mapping

#: Keys the public ``branch(hints: dict)`` boundary accepts.
ACCEPTED_BRANCH_HINT_KEYS = frozenset({"isolated", "__restore__"})

#: Keys accepted from a public fork-hints mapping. ``__restore__`` is
#: coordinator-internal — restore is a first-class field on ``ForkHints``,
#: set only by the restore path; the dunder key does not pass through the
#: typed layer.
ACCEPTED_FORK_HINT_KEYS = frozenset({"isolated"})


def validate_branch_hints(hints: Mapping[str, Any] | None) -> None:
    """Reject unknown keys at the substrate ``branch()`` boundary."""
    if not hints:
        return
    unknown = sorted(set(hints) - ACCEPTED_BRANCH_HINT_KEYS)
    if unknown:
        raise UnknownForkHintError(
            f"Unknown branch hint key(s) {unknown!r}; accepted keys: {sorted(ACCEPTED_BRANCH_HINT_KEYS)!r}."
        )


@dataclass(frozen=True)
class ForkHints:
    """Typed fork hints for scope creation.

    ``isolated`` keeps recording-only (non-isolated) forks expressible.
    ``restore`` is a first-class field set only by the coordinator's
    restore path; it is not accepted from public mappings.
    """

    isolated: bool = False
    restore: bool = False

    @classmethod
    def from_value(cls, value: ForkHints | Mapping[str, Any] | None) -> ForkHints:
        """Normalize a public ``hints`` value; a misspelled key is a crash here."""
        if value is None:
            return cls()
        if isinstance(value, ForkHints):
            return value
        unknown = sorted(set(value) - ACCEPTED_FORK_HINT_KEYS)
        if unknown:
            raise UnknownForkHintError(
                f"Unknown fork hint key(s) {unknown!r}; accepted keys: {sorted(ACCEPTED_FORK_HINT_KEYS)!r}."
            )
        return cls(isolated=bool(value.get("isolated", False)))

    def to_branch_hints(self) -> dict[str, Any] | None:
        """Lower to the dict shape of the public ``branch(hints)`` boundary."""
        hints: dict[str, Any] = {}
        if self.isolated:
            hints["isolated"] = True
        if self.restore:
            hints["__restore__"] = True
        return hints or None
