"""Mutable runtime-only state for ScopeProxy."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ._provider_state import ProviderState

if TYPE_CHECKING:
    from shepherd_core.foundation.protocols.device import DeviceProtocol

    from .scope import ScopeProxy

__all__ = ["ScopeRuntimeState"]


@dataclass
class ScopeRuntimeState:
    """Mutable process state that should not live in ImmutableScope."""

    parent_proxy: ScopeProxy | None = None
    token_stack: list[Any] = field(default_factory=list)
    depth: int = 0
    resumed_layers: list[Any] | None = None
    materialized_index: int = 0
    is_discarded: bool = False
    is_materialized: bool = False
    device: DeviceProtocol | None = None
    exited: bool = False
    provider_state: ProviderState = field(default_factory=ProviderState)
    emit_lock: threading.Lock = field(default_factory=threading.Lock)
