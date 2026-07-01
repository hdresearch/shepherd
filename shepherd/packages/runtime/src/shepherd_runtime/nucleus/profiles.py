"""Minimal task effect-surface profiles for the first runtime bridge."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EffectSurfaceProfile:
    """Named runtime effect-surface profile accepted by ``@task(may=...)``.

    This is intentionally smaller than the target ``Match`` algebra. The first
    integration slice only needs stable names that can lower to vcs-core
    ``ActiveSurface`` profiles.
    """

    name: str

    def __post_init__(self) -> None:
        if self.name not in {"ReadOnly", "Permissive"}:
            raise ValueError(f"unknown effect-surface profile {self.name!r}")

    def __repr__(self) -> str:
        return self.name


ReadOnly = EffectSurfaceProfile("ReadOnly")
Permissive = EffectSurfaceProfile("Permissive")


def coerce_effect_surface_profile(value: object | None) -> EffectSurfaceProfile | None:
    """Validate the minimal ``may=`` value surface accepted in this tranche."""
    if value is None:
        return None
    if isinstance(value, EffectSurfaceProfile):
        return value
    raise TypeError("@task(may=...) currently accepts only ReadOnly or Permissive")


__all__ = [
    "EffectSurfaceProfile",
    "Permissive",
    "ReadOnly",
    "coerce_effect_surface_profile",
]
