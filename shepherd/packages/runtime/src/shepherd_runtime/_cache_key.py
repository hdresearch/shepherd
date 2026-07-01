"""Runtime-owned execution key computation for cache lookup."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from shepherd_core.errors import ProviderNotFoundError

from ._cache_policy import CachePolicy, HashingScope

if TYPE_CHECKING:
    from collections.abc import Sequence

    from shepherd_runtime.scope import ScopeProxy
    from shepherd_runtime.scope_types import BindingViewLike
    from shepherd_runtime.task.metadata import TaskMetadata

logger = logging.getLogger(__name__)


def _hash_str(s: str, length: int = 12) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:length]


def _json_serialize_inputs(inputs: dict[str, Any]) -> str:
    from shepherd_runtime.task.source_analysis import SourceExtractionError, extract_task_source

    def serialize_value(v: Any) -> Any:
        if isinstance(v, (str, int, float, bool, type(None))):
            return v
        if isinstance(v, type) and hasattr(v, "_task_meta"):
            try:
                return {"__task_source__": extract_task_source(v)}
            except SourceExtractionError:
                return repr(v)
        if isinstance(v, (list, tuple)):
            return [serialize_value(x) for x in v]
        if isinstance(v, dict):
            return {k: serialize_value(val) for k, val in sorted(v.items())}
        return repr(v)

    serializable = {k: serialize_value(v) for k, v in sorted(inputs.items())}
    return json.dumps(serializable, sort_keys=True, separators=(",", ":"))


def _compute_task_key(meta: TaskMetadata, policy: CachePolicy) -> str:
    components = [meta.name]
    if policy == CachePolicy.STRICT:
        components.append(meta.docstring or "")

    for name, field_info in sorted(meta.inputs.items()):
        type_name = getattr(field_info.inner_type, "__name__", str(field_info.inner_type))
        components.append(f"input:{name}:{type_name}")

    for name, field_info in sorted(meta.outputs.items()):
        type_name = getattr(field_info.inner_type, "__name__", str(field_info.inner_type))
        components.append(f"output:{name}:{type_name}")

    for name, field_info in sorted(meta.contexts.items()):
        type_name = getattr(field_info.inner_type, "__name__", str(field_info.inner_type))
        components.append(f"context:{name}:{type_name}")

    if meta.guidance:
        components.append(f"guidance:{meta.guidance}")

    return _hash_str("|".join(components))


def _compute_contexts_hash(bindings: Sequence[BindingViewLike], policy: CachePolicy) -> str:
    if policy == CachePolicy.INPUTS_ONLY:
        return "0" * 16

    components = []
    hashing_scope = HashingScope.FULL if policy == CachePolicy.STRICT else HashingScope.TRACKED_ONLY

    for binding in sorted(bindings, key=lambda b: b.name):
        ctx = binding.context
        if hasattr(ctx, "state_hash") and callable(ctx.state_hash):
            ctx_hash = ctx.state_hash(hashing_scope)
            components.append(f"{binding.name}:{ctx_hash}")
        else:
            components.append(f"{binding.name}:{ctx.context_id}")

    if not components:
        return "0" * 16

    return _hash_str("|".join(components), length=16)


def _compute_provider_hash(provider: Any) -> str:
    if hasattr(provider, "model"):
        return _hash_str(f"{type(provider).__name__}:{provider.model}", length=8)
    return _hash_str(type(provider).__name__, length=8)


@dataclass(frozen=True)
class ExecutionKey:
    """Fingerprint of a task execution for cache lookup."""

    task_name: str
    task_key: str
    inputs_hash: str
    contexts_hash: str
    provider_hash: str

    @property
    def key(self) -> str:
        components = [
            self.task_name,
            self.task_key,
            self.inputs_hash,
            self.contexts_hash,
            self.provider_hash,
        ]
        return _hash_str("|".join(components), length=16)

    @classmethod
    def compute(
        cls,
        meta: TaskMetadata,
        inputs: dict[str, Any],
        scope: ScopeProxy,
        policy: CachePolicy,
    ) -> ExecutionKey:
        task_key = _compute_task_key(meta, policy)
        inputs_hash = _hash_str(_json_serialize_inputs(inputs))

        bindings = list(scope.all_bindings())
        context_bindings = [b._binding if hasattr(b, "_binding") else b for b in bindings]
        contexts_hash = _compute_contexts_hash(context_bindings, policy)  # type: ignore[arg-type]

        try:
            provider = scope.get_provider()
            provider_hash = _compute_provider_hash(provider)
        except ProviderNotFoundError:
            provider_hash = "00000000"
        except Exception:  # noqa: BLE001
            logger.warning("Unexpected error computing provider hash for cache key", exc_info=True)
            provider_hash = "00000000"

        return cls(
            task_name=meta.name,
            task_key=task_key,
            inputs_hash=inputs_hash,
            contexts_hash=contexts_hash,
            provider_hash=provider_hash,
        )

    def __str__(self) -> str:
        return f"ExecutionKey({self.task_name}:{self.key})"


__all__ = [
    "ExecutionKey",
]
