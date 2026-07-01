"""Immutable views for validator-facing ingress payloads."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any


def immutable_payload_view(value: Any) -> Any:
    """Return a recursively immutable copy for validation hooks.

    Container values are copied into read-only shapes so validators cannot
    mutate the canonical dispatch payload through a nested reference. Opaque
    leaf objects are intentionally preserved by identity.
    """
    if isinstance(value, Mapping):
        return MappingProxyType({key: immutable_payload_view(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(immutable_payload_view(item) for item in value)
    if isinstance(value, list):
        return tuple(immutable_payload_view(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(immutable_payload_view(item) for item in value)
    if isinstance(value, set):
        return frozenset(immutable_payload_view(item) for item in value)
    return value
