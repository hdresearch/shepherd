"""Public ``shepherd.effects`` namespace.

Per the CONTRACTS Public Re-Export Map: effect base classes
(``Ask``, ``Tell``, ``Resumption``) sit at ``shepherd.effects``
because authors subclass them rarely; the import cost is one extra
line and the namespace separation is worth it.

Pinned by `docs/design/proposed/260505-plans/CONTRACTS.md`
"Public Re-Export Map".
"""

from __future__ import annotations

from shepherd_runtime.effects import (
    Ask,
    Resumption,
    ResumptionAborted,
    ResumptionConsumed,
    Tell,
)

# Ordered by the authoring concept flow, not alphabetically.
__all__ = [  # noqa: RUF022
    "Ask",
    "Tell",
    "Resumption",
    "ResumptionAborted",
    "ResumptionConsumed",
]
