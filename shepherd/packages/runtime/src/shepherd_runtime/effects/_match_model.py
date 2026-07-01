"""Private syntax model shared by the public policy facade and matcher proof core."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "FieldPredicate",
    "KindPattern",
    "Node",
    "Predicate",
    "Subset",
]


class Subset(Enum):
    """Three-valued containment result."""

    Yes = "yes"
    No = "no"
    Unknown = "unknown"

    def __and__(self, other: Subset) -> Subset:
        if self is Subset.No or other is Subset.No:
            return Subset.No
        if self is Subset.Unknown or other is Subset.Unknown:
            return Subset.Unknown
        return Subset.Yes


@dataclass(frozen=True)
class KindPattern:
    mode: str
    kind: str
    cls: type | None = None


@dataclass(frozen=True)
class FieldPredicate:
    name: str
    op: str
    value: Any


@dataclass(frozen=True)
class Predicate:
    fn: object


@dataclass(frozen=True)
class Node:
    tag: str
    args: tuple[object, ...] = ()
