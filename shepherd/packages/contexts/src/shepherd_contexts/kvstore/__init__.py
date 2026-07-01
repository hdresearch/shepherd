"""KVStore context for simple key-value storage.

This module provides KVStoreContext, a simple key-value store
execution context for configuration and state management.

Example:
    from shepherd_contexts.kvstore import KVStoreContext

    store = KVStoreContext.create({"user": "alice", "count": "0"})
    store = store.prepare()
    store.set("count", "1")
    # Changes are captured during task execution
"""

from shepherd_contexts.kvstore.effects import KeyDeleted, KeySet
from shepherd_contexts.kvstore.store import KVStoreContext

__all__ = [
    "KVStoreContext",
    "KeyDeleted",
    "KeySet",
]
