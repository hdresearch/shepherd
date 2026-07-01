"""The @package declarative metadata abstraction.

Provides a single decorator for package ``__init__.py`` modules that declares
where to find tasks, contexts, and effects — replacing per-task entry-point
lines, domain marker classes, and ``__init__.py`` re-export chains.

Usage::

    # shepherd_banking/__init__.py
    from shepherd_core.package import package

    @package(
        name="banking",
        version="0.1.0",
        tasks=["shepherd_banking.tasks"],
        contexts=["shepherd_banking.contexts"],
        effects=["shepherd_banking.contexts.effects"],
    )
    def banking():
        \"\"\"Transfer funds, query balances, and manage accounts.\"\"\"
"""

from __future__ import annotations

import logging
from collections.abc import Callable  # noqa: TC003 - needed at runtime for get_type_hints()
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, PackageInfo] = {}


@dataclass(frozen=True)
class PackageInfo:
    """Immutable metadata for a registered package."""

    name: str
    version: str
    doc: str
    task_modules: tuple[str, ...]
    context_modules: tuple[str, ...]
    effect_modules: tuple[str, ...]
    requires: tuple[str, ...]


def package(
    *,
    name: str,
    version: str,
    tasks: list[str],
    contexts: list[str] | None = None,
    effects: list[str] | None = None,
    requires: list[str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declarative metadata annotation for a package.

    Args:
        name: Unique package identifier.
        version: Semver version string.
        tasks: Dotted module paths to walk for ``@task`` classes.
        contexts: Dotted module paths to walk for ``BindableContext`` subclasses.
        effects: Dotted module paths to walk for ``@register_effect`` classes.
        requires: Other package names that must be present.

    Returns:
        Decorator that registers the package and attaches ``PackageInfo``.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        info = PackageInfo(
            name=name,
            version=version,
            doc=fn.__doc__ or "",
            task_modules=tuple(tasks),
            context_modules=tuple(contexts or ()),
            effect_modules=tuple(effects or ()),
            requires=tuple(requires or ()),
        )
        _REGISTRY[name] = info
        fn._package_info = info  # type: ignore[attr-defined]
        return fn

    return decorator


def get_package_registry() -> dict[str, PackageInfo]:
    """Return the current package registry (read-only copy)."""
    return dict(_REGISTRY)


def discover_packages() -> dict[str, PackageInfo]:
    """Discover all registered packages.

    Loads entry points from the ``shepherd.packages`` group, triggering
    ``@package`` decorators which populate ``_REGISTRY``. Returns a dict
    mapping package name to ``PackageInfo``.
    """
    from importlib.metadata import entry_points

    for ep in entry_points(group="shepherd.packages"):
        try:
            ep.load()  # triggers @package decorator, populates _REGISTRY
        except Exception:  # noqa: BLE001
            logger.warning(
                "Failed to load package '%s'",
                ep.name,
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )

    return dict(_REGISTRY)


__all__ = [
    "PackageInfo",
    "discover_packages",
    "get_package_registry",
    "package",
]
