"""The model-call boundary types the nucleus's ``deliver`` rides (W1).

The dialect-native shape of the legacy ``shepherd_runtime.provider_boundary``
pair: a ``deliver(Type, goal=…, evidence=…)`` call becomes a ``ModelRequest``
dispatched to the installed ``handle("model.call", …)`` responder (offline
pattern) — or, when live providers grow a model seam (Phase E command lane),
to the provider. ``ModelResponse.structured_output`` carries the typed value
under the ``"result"`` key (the quickstart's no-result-key Failed observable).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ModelRequest", "ModelResponse"]


@dataclass(frozen=True)
class ModelRequest:
    """One ``deliver`` call's request to the model seam."""

    goal: str
    evidence: tuple[Any, ...] = ()


@dataclass(frozen=True)
class ModelResponse:
    """The model seam's reply; the typed value rides ``structured_output["result"]``."""

    structured_output: dict[str, Any]
