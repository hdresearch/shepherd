"""KVStore-specific effects.

These effects are emitted by KVStoreContext during execution capture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from shepherd_core.effects import Effect

if TYPE_CHECKING:
    from collections.abc import Mapping


class KeySet(Effect):
    """A key was set in the key-value store.

    Emitted when a key is created or updated in the store.
    """

    effect_type: Literal["key_set"] = "key_set"
    key: str = ""
    old_value: str | None = None
    new_value: str = ""


class KeyDeleted(Effect):
    """A key was deleted from the key-value store.

    Emitted when a key is removed from the store.
    """

    effect_type: Literal["key_deleted"] = "key_deleted"
    key: str = ""
    had_value: str = ""


def get_effect_types() -> Mapping[str, type[Effect]]:
    """Return the explicit effect contributor surface for runtime decode."""
    return {
        "key_deleted": KeyDeleted,
        "key_set": KeySet,
    }


__all__ = [
    "KeyDeleted",
    "KeySet",
    "get_effect_types",
]
