"""Phase 1 effects runtime surface plus legacy registry re-export.

Two responsibilities live in this package:

1. **Typed-effect surface** (CONTRACTS B1-B5, C1, C2, C6):
   ``Ask``, ``Tell``, ``Resumption``, ``handle``, ``ask``, ``tell``,
   plus the metaclass and effect-kind validators. ``handle()`` installs
   contextvar-scoped pure-response handlers, ``ask()`` dispatches to the
   nearest matching handler or raises ``UnhandledAsk``, and ``tell()``
   dispatches when handled or follows the effect's unhandled policy. Durable
   supervisor/resumption semantics remain future work.

2. **Legacy effect registry** (D5 deletion target): the
   ``compose_effect_registry`` / ``decode_effect`` plumbing for the
   pre-Plan-04 ``Effect`` class hierarchy. Kept under the same
   namespace so existing callers (Plan 00, persistence, export,
   contexts) keep working until the Plan 02 wave-2 deletion lands.

Pinned by `docs/design/proposed/260505-plans/CONTRACTS.md` Group B/C.
"""

from __future__ import annotations

from shepherd_runtime.effects.ask_tell import (
    HandlerSignatureError,
    UnhandledAsk,
    UnhandledEffect,
    UnhandledTell,
    ask,
    sync_ask,
    sync_tell,
    tell,
)
from shepherd_runtime.effects.base import Ask, Tell, _EffectMeta
from shepherd_runtime.effects.effect_kind import (
    ConflictingKind,
    EffectIdentity,
    effect_key_for_class,
    effect_key_for_event,
    is_explicit_effect_kind,
    model_kind,
    parse_matcher_kind_sugar,
    register_effect_class,
    split_effect_kind,
    tool_kind,
    validate_public_effect_kind,
)
from shepherd_runtime.effects.handle import handle
from shepherd_runtime.effects.policy import (
    EffectNotPermitted,
    EffectSurfaceEmpty,
    EffectSurfaceTooWide,
    Installation,
    Match,
    OverbroadHandler,
    Plan,
    PlanNotExtractable,
    Subset,
)
from shepherd_runtime.effects.registry import (
    EFFECTS_GROUP,
    EffectContributorConflictError,
    EffectContributorNameConflictError,
    EffectContributorValidationError,
    compose_effect_registry,
    decode_effect,
    discover_effect_types,
)
from shepherd_runtime.effects.resumption import (
    Resumption,
    ResumptionAborted,
    ResumptionConsumed,
)
from shepherd_runtime.effects.shape_detection import (
    HandlerShape,
    detect_handler_shape,
)

__all__ = [  # noqa: RUF022
    # Base classes (B1, B2)
    "Ask",
    "Tell",
    "_EffectMeta",
    "ConflictingKind",
    "EffectIdentity",
    # Resumption Protocol (B3)
    "Resumption",
    "ResumptionAborted",
    "ResumptionConsumed",
    # Effect-kind naming (B5)
    "split_effect_kind",
    "tool_kind",
    "model_kind",
    "validate_public_effect_kind",
    "parse_matcher_kind_sugar",
    "register_effect_class",
    "effect_key_for_class",
    "effect_key_for_event",
    "is_explicit_effect_kind",
    # handle() (C1)
    "handle",
    # ask / tell (C1 perform side)
    "ask",
    "tell",
    "sync_ask",
    "sync_tell",
    # Effect-surface policy values (W3.B-iii Path-A core)
    "Match",
    "Plan",
    "Installation",
    "Subset",
    # Errors
    "EffectNotPermitted",
    "EffectSurfaceEmpty",
    "EffectSurfaceTooWide",
    "UnhandledAsk",
    "UnhandledEffect",
    "UnhandledTell",
    "OverbroadHandler",
    "PlanNotExtractable",
    "HandlerSignatureError",
    # Shape detection (C2)
    "HandlerShape",
    "detect_handler_shape",
    # Legacy registry (D5 deletion target)
    "EFFECTS_GROUP",
    "EffectContributorConflictError",
    "EffectContributorNameConflictError",
    "EffectContributorValidationError",
    "compose_effect_registry",
    "decode_effect",
    "discover_effect_types",
]
