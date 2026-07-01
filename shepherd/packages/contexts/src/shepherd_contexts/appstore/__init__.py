"""App Store context for App Store Connect API access.

This module provides AppStoreContext, which enables access to
App Store Connect API for fetching reports and analytics data.

Example:
    from shepherd_contexts.appstore import AppStoreContext

    appstore = AppStoreContext(
        issuer_id="your-issuer-id",
        key_id="your-key-id",
        vendor_number="your-vendor-number",
        app_ids=frozenset({"app1", "app2"}),
    )
"""

from shepherd_contexts.appstore.context import AppStoreContext
from shepherd_contexts.appstore.effects import AppStoreAPICall

__all__ = [
    "AppStoreAPICall",
    "AppStoreContext",
]
