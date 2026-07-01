"""Database-specific effects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from shepherd_core.effects import Effect

if TYPE_CHECKING:
    from collections.abc import Mapping


class QueryExecuted(Effect):
    """Database query was executed."""

    effect_type: Literal["query_executed"] = "query_executed"
    database: str = ""
    query_type: str = ""  # SELECT, INSERT, UPDATE, DELETE
    table: str = ""
    row_count: int = 0


def get_effect_types() -> Mapping[str, type[Effect]]:
    """Return the explicit effect contributor surface for runtime decode."""
    return {
        "query_executed": QueryExecuted,
    }


__all__ = [
    "QueryExecuted",
    "get_effect_types",
]
