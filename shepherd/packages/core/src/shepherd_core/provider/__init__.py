"""Layer 3: Provider - Abstract base for LLM SDK adapters.

This package provides the Provider abstract base class that defines
the interface for LLM SDK adapters (Claude, OpenAI, etc.).
"""

from __future__ import annotations

from .provider import Provider
from .runtime import (
    DefaultProviderRuntime,
    EffectSink,
    ProviderRuntime,
)

__all__ = [
    "DefaultProviderRuntime",
    "EffectSink",
    "Provider",
    "ProviderRuntime",
]
