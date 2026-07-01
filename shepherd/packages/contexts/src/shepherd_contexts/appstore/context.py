"""AppStoreContext: App Store Connect API access for reports and data.

This reference implementation demonstrates:
- Read-only external API access
- AUTO reversibility (read-only operations)
- Custom tool definitions for App Store queries
- Domain-specific effect capture
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Self

from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
    ToolCall,
    ToolDefinition,
    ValidationResult,
)
from shepherd_runtime.context import BindableContext

from shepherd_contexts.appstore.effects import AppStoreAPICall

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from shepherd_core.effects import Effect
    from shepherd_runtime.context import Sandbox


@dataclass
class AppStoreContext(BindableContext):
    """App Store Connect API context for fetching reports and data.

    Demonstrates:
    - Read-only external API access
    - AUTO reversibility (queries don't change state)
    - Custom tools for App Store data (SalesReport, SubscriptionReport)
    - API call tracking as effects

    Lifecycle:
        configure(): Return binding with App Store query tools
        prepare(): Authenticate with API (in real implementation)
        extract_effects(): Parse tool calls to emit AppStoreAPICall effects
        apply_effect(): No-op (audit effects don't change context config)
        cleanup(): No-op
    """

    __binding_name__: ClassVar[str] = "appstore"

    issuer_id: str
    key_id: str = ""
    vendor_number: str = ""
    app_ids: frozenset[str] = field(default_factory=frozenset)

    @property
    def context_id(self) -> str:
        return f"appstore:{self.issuer_id}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        """Read-only API access is AUTO reversible."""
        return ReversibilityLevel.AUTO

    def __str__(self) -> str:
        """Visible - App Store context should be in prompts."""
        lines = [
            "App Store Connect API access",
            f"Issuer: {self.issuer_id}",
        ]
        if self.vendor_number:
            lines.append(f"Vendor: {self.vendor_number}")
        if self.app_ids:
            lines.append(f"Apps: {', '.join(sorted(self.app_ids))}")
        lines.append("Available tools: SalesReport, SubscriptionReport, AppAnalytics")
        return "\n".join(lines)

    # === Configuration ===

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        """Return binding with App Store query tools."""
        custom_tools = self._build_tools()

        return ProviderBinding(
            context_id=self.context_id,
            context_type="AppStoreContext",
            context_description=str(self),
            custom_tools=tuple(custom_tools),
            validate_tool=self._make_validator(),
            trust_level="standard",  # Read-only, standard trust
        )

    def _build_tools(self) -> list[ToolDefinition]:
        """Build custom App Store tools."""
        return [
            ToolDefinition(
                name="SalesReport",
                description="Fetch sales and downloads report for a date range",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Start date (YYYY-MM-DD)",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date (YYYY-MM-DD)",
                        },
                        "report_type": {
                            "type": "string",
                            "enum": ["SALES", "SUBSCRIPTION", "SUBSCRIPTION_EVENT"],
                            "description": "Type of report to fetch",
                        },
                    },
                    "required": ["start_date", "end_date"],
                },
                handler=self._handle_sales_report,
            ),
            ToolDefinition(
                name="SubscriptionReport",
                description="Fetch subscription metrics and retention data",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Start date (YYYY-MM-DD)",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date (YYYY-MM-DD)",
                        },
                        "app_id": {
                            "type": "string",
                            "description": "Specific app ID (optional)",
                        },
                    },
                    "required": ["start_date", "end_date"],
                },
                handler=self._handle_subscription_report,
            ),
            ToolDefinition(
                name="AppAnalytics",
                description="Fetch app analytics (impressions, downloads, revenue)",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {
                            "type": "string",
                            "description": "Start date (YYYY-MM-DD)",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "End date (YYYY-MM-DD)",
                        },
                        "metrics": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Metrics to fetch (impressions, downloads, revenue)",
                        },
                    },
                    "required": ["start_date", "end_date"],
                },
                handler=self._handle_analytics,
            ),
        ]

    def _handle_sales_report(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle sales report query (mock)."""
        return {
            "report_type": params.get("report_type", "SALES"),
            "start_date": params["start_date"],
            "end_date": params["end_date"],
            "records": [
                {"date": params["start_date"], "units": 150, "revenue": 1499.50},
                {"date": params["end_date"], "units": 175, "revenue": 1749.25},
            ],
            "total_units": 325,
            "total_revenue": 3248.75,
        }

    def _handle_subscription_report(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle subscription report query (mock)."""
        return {
            "start_date": params["start_date"],
            "end_date": params["end_date"],
            "active_subscriptions": 5000,
            "new_subscriptions": 250,
            "churned": 75,
            "retention_rate": 0.85,
        }

    def _handle_analytics(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle analytics query (mock)."""
        return {
            "start_date": params["start_date"],
            "end_date": params["end_date"],
            "impressions": 50000,
            "downloads": 1200,
            "revenue": 15000.00,
            "conversion_rate": 0.024,
        }

    def _make_validator(self) -> Callable[[ToolCall], ValidationResult]:
        """Create validator for App Store tools."""

        def validate(tool: ToolCall) -> ValidationResult:
            # Validate app_id if specified and we have restrictions
            if self.app_ids and tool.name in ("SalesReport", "SubscriptionReport"):
                app_id = tool.params.get("app_id")
                if app_id and app_id not in self.app_ids:
                    return ValidationResult.reject(tool, f"App ID {app_id} not in allowed apps")

            return ValidationResult.allow(tool)

        return validate

    # === v2 API ===

    def extract_effects(
        self,
        sandbox: Sandbox | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        """Extract API calls as effects from tool calls.

        Parses the execution result to identify App Store API tool calls,
        emitting corresponding audit effects.

        Args:
            sandbox: Not used (App Store context has no filesystem operations)
            result: Execution result containing tool calls

        Returns:
            Sequence of AppStoreAPICall effects
        """
        effects: list[Effect] = []
        seen_calls: set[tuple[str, str, str | None]] = set()

        for call, res in zip(result.tool_calls, result.tool_results, strict=False):
            if not res.success:
                continue

            if call.name in ("SalesReport", "SubscriptionReport", "AppAnalytics"):
                endpoint = call.name.lower()
                start_date = call.params.get("start_date", "")
                end_date = call.params.get("end_date", "")
                date_range = f"{start_date} to {end_date}"
                app_id = call.params.get("app_id")
                call_key = (endpoint, date_range, app_id)

                if call_key not in seen_calls:
                    seen_calls.add(call_key)
                    effects.append(
                        AppStoreAPICall(
                            endpoint=endpoint,
                            app_id=app_id,
                            date_range=date_range,
                            data_type=call.params.get("report_type", "analytics"),
                            context_id=self.context_id,
                        )
                    )

        return effects

    def apply_effect(self, effect: Effect) -> Self:
        """Apply effect to derive new context state.

        AppStoreContext configuration doesn't change based on operations -
        AppStoreAPICall effects are audit records only.

        Args:
            effect: The effect to apply

        Returns:
            Self unchanged (audit effects don't modify context config)
        """
        return self


__all__ = ["AppStoreContext"]
