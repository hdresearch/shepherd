"""Explicit effect contributor discovery for runtime and core decode surfaces."""

from __future__ import annotations

import logging
import types
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any

from shepherd_core.config import is_strict_mode
from shepherd_core.errors import PluginLoadError

from .effects import Effect

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)

EFFECTS_GROUP = "shepherd.effects"


class EffectContributorConflictError(ValueError):
    """Raised when effect contributors define conflicting effect_type keys."""

    def __init__(self, effect_type: str, existing_owner: str, new_owner: str) -> None:
        super().__init__(f"Effect type '{effect_type}' was contributed by both '{existing_owner}' and '{new_owner}'")
        self.effect_type = effect_type
        self.existing_owner = existing_owner
        self.new_owner = new_owner


class EffectContributorNameConflictError(ValueError):
    """Raised when multiple contributors declare the same entry-point name."""

    def __init__(self, name: str, existing_owner: str, new_owner: str) -> None:
        super().__init__(
            f"Effect contributor entry point '{name}' was declared by both '{existing_owner}' and '{new_owner}'"
        )
        self.name = name
        self.existing_owner = existing_owner
        self.new_owner = new_owner


class EffectContributorValidationError(ValueError):
    """Raised when a contributor violates the explicit effect plugin contract."""


def _iter_entry_points(group: str) -> Iterable[Any]:
    return tuple(entry_points(group=group))


def _effect_type_for_class(effect_cls: type[Effect]) -> str:
    effect_type_field = effect_cls.model_fields.get("effect_type")
    if effect_type_field is None or effect_type_field.default is None:
        raise ValueError(f"Effect class {effect_cls.__name__} must define a default effect_type")
    return str(effect_type_field.default)


def _normalize_effect_contributor(name: str, contributor: Any) -> Mapping[str, type[Effect]]:
    normalized: Mapping[str, type[Effect]]
    if isinstance(contributor, type) and issubclass(contributor, Effect):
        normalized = {_effect_type_for_class(contributor): contributor}
        return _validate_effect_mapping(name, normalized)

    get_effect_types = getattr(contributor, "get_effect_types", None)
    if callable(get_effect_types):
        normalized = dict(get_effect_types())
        return _validate_effect_mapping(name, normalized)

    contributor_type = type(contributor).__name__
    if isinstance(contributor, types.ModuleType):
        contributor_type = f"module {contributor.__name__}"

    raise TypeError(
        f"Effect contributor '{name}' must resolve to an Effect subclass or expose get_effect_types(); "
        f"got {contributor_type}"
    )


def _validate_effect_mapping(name: str, mapping: Mapping[Any, Any]) -> dict[str, type[Effect]]:
    validated: dict[str, type[Effect]] = {}

    for effect_type, effect_cls in mapping.items():
        if not isinstance(effect_type, str) or not effect_type:
            raise EffectContributorValidationError(
                f"Effect contributor '{name}' must map non-empty string effect_type keys; got {effect_type!r}"
            )
        if not isinstance(effect_cls, type) or not issubclass(effect_cls, Effect):
            raise EffectContributorValidationError(
                f"Effect contributor '{name}' must map effect types to Effect subclasses; got {effect_cls!r}"
            )

        declared_effect_type = _effect_type_for_class(effect_cls)
        if declared_effect_type != effect_type:
            raise EffectContributorValidationError(
                f"Effect contributor '{name}' mapped '{effect_type}' to {effect_cls.__name__}, "
                f"but the class declares effect_type '{declared_effect_type}'"
            )

        validated[effect_type] = effect_cls

    return validated


def discover_effects_with_owners() -> tuple[dict[str, type[Effect]], dict[str, str]]:
    """Discover effect contributors and preserve contributor ownership metadata."""
    result: dict[str, type[Effect]] = {}
    contributor_by_type: dict[str, str] = {}
    contributor_by_name: dict[str, str] = {}

    for ep in sorted(_iter_entry_points(EFFECTS_GROUP), key=lambda candidate: (candidate.name, candidate.value)):
        name = ep.name
        contributor_owner = getattr(ep, "value", name)
        try:
            existing_name_owner = contributor_by_name.get(name)
            if existing_name_owner is not None:
                raise EffectContributorNameConflictError(name, existing_name_owner, contributor_owner)

            contributor_by_name[name] = contributor_owner
            contributor = ep.load()
            for effect_type, effect_cls in _normalize_effect_contributor(name, contributor).items():
                existing_owner = contributor_by_type.get(effect_type)
                if existing_owner is not None:
                    raise EffectContributorConflictError(effect_type, existing_owner, name)
                result[effect_type] = effect_cls
                contributor_by_type[effect_type] = name
        except (
            EffectContributorConflictError,
            EffectContributorNameConflictError,
            EffectContributorValidationError,
        ):
            raise
        except Exception as error:
            if is_strict_mode():
                raise PluginLoadError(name, EFFECTS_GROUP, error) from error
            logger.warning(
                "Failed to load effect contributor '%s': %s",
                name,
                error,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    return result, contributor_by_type


def discover_effects() -> dict[str, type[Effect]]:
    """Discover effect contributors and normalize them to concrete effect types."""
    result, _ = discover_effects_with_owners()
    return result


__all__ = [
    "EFFECTS_GROUP",
    "EffectContributorConflictError",
    "EffectContributorNameConflictError",
    "EffectContributorValidationError",
    "discover_effects",
    "discover_effects_with_owners",
]
