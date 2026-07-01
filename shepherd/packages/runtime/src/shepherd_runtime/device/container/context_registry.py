"""Runtime-owned context registry for container state deserialization.

Thin delegation to ``shepherd_runtime.registry`` so that context modules
which self-register at import time (e.g. ``shepherd_contexts.workspace.ref``)
and the container task-runner both operate on the same global dict.
"""

from __future__ import annotations

from shepherd_runtime.registry import (
    _CONTEXT_DESERIALIZERS,
    ContextDeserializationError,
    ContextDeserializer,
    deserialize_all_contexts,
    deserialize_context,
    get_context_deserializer,
    register_context_deserializer,
)
from shepherd_runtime.registry import (
    list_registered_context_types as list_registered_types,
)

__all__ = [
    "_CONTEXT_DESERIALIZERS",
    "ContextDeserializationError",
    "ContextDeserializer",
    "deserialize_all_contexts",
    "deserialize_context",
    "get_context_deserializer",
    "list_registered_types",
    "register_context_deserializer",
]
