"""Contiguous hook-event progress tracking for the session daemon."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HookEventFrontier:
    """Track accepted hook events and the contiguous processed frontier.

    Hook dispatch can finish out of order because each hook connection is
    handled on its own thread. A max processed watermark is therefore unsafe:
    seq 2 finishing does not imply seq 1 finished. This tracker only advances
    the public processed frontier when every prior accepted seq is terminal.
    """

    accepted_seq: int = 0
    processed_seq: int = 0
    _terminal: set[int] = field(default_factory=set)

    def accept_next(self) -> int:
        self.accepted_seq += 1
        return self.accepted_seq

    def mark_terminal(self, seq: int) -> None:
        if seq <= 0:
            return
        self._terminal.add(seq)
        self._advance()

    def _advance(self) -> None:
        while self.processed_seq + 1 in self._terminal:
            self.processed_seq += 1
            self._terminal.remove(self.processed_seq)
