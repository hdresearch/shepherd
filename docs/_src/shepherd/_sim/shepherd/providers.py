"""SIMULATION SHIM — provider factories ( `from shepherd.providers import claude`)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _Provider:
    name: str
    model: str


def claude(model: str) -> _Provider:
    """Select the Claude provider (simulation: returns an inert token)."""
    return _Provider(name="claude", model=model)
