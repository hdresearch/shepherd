"""App Store-specific effects.

These effects are emitted by AppStoreContext during execution capture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from shepherd_core.effects import Effect

if TYPE_CHECKING:
    from collections.abc import Mapping


class AppStoreAPICall(Effect):
    """App Store Connect API was called.

    Emitted when the App Store Connect API is accessed for reports,
    analytics, or other data retrieval operations.
    """

    effect_type: Literal["appstore_api_call"] = "appstore_api_call"
    endpoint: str = ""  # "sales_report", "subscription_report", "analytics"
    app_id: str | None = None
    date_range: str = ""  # "YYYY-MM-DD to YYYY-MM-DD"
    data_type: str = ""  # What type of data was retrieved


def get_effect_types() -> Mapping[str, type[Effect]]:
    """Return the explicit effect contributor surface for runtime decode."""
    return {
        "appstore_api_call": AppStoreAPICall,
    }


__all__ = [
    "AppStoreAPICall",
    "get_effect_types",
]
