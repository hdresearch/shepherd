"""BankingContext: Financial operations with transfer controls.

This context demonstrates:
- COMPENSABLE reversibility (transfers need compensation to reverse)
- Custom tool definitions (BankBalance, BankTransfer)
- Strict validation based on configuration
- Domain-specific effect capture
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Self

from shepherd_core import (
    Effect,
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ReversibilityLevel,
    ToolCall,
    ToolDefinition,
    ValidationResult,
)
from shepherd_runtime.context import BindableContext

from .effects import BalanceQueried, TransferInitiated

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from shepherd_runtime.context import Sandbox


@dataclass
class BankingContext(BindableContext):
    """Financial operations context with transfer controls.

    Provides domain-specific context with:
    - Configurable transfer permissions
    - Transfer limits
    - Custom banking tools (BankBalance, BankTransfer)
    - COMPENSABLE reversibility when transfers enabled

    Lifecycle:
        configure(): Return binding with custom tools
        prepare(): Authenticate (in real implementation)
        extract_effects(): Parse tool calls to emit TransferInitiated/BalanceQueried
        apply_effect(): No-op (audit effects don't change context config)
        cleanup(): No-op

    Example:
        # Read-only banking context
        banking = BankingContext(
            account_id="ACC-001",
            account_name="Operations",
        )

        # Transfer-enabled context
        banking = BankingContext(
            account_id="ACC-001",
            account_name="Operations",
            allow_transfers=True,
            transfer_limit=10000.0,
        )

        # Bind to scope by type
        scope.bind(banking)
    """

    __binding_name__: ClassVar[str] = "banking"

    account_id: str
    account_name: str = ""
    allow_transfers: bool = False
    transfer_limit: float = 10000.0
    currency: str = "USD"

    @property
    def context_id(self) -> str:
        mode = "transfer" if self.allow_transfers else "readonly"
        return f"banking:{self.account_id}:{mode}"

    @property
    def reversibility(self) -> ReversibilityLevel:
        """Transfers are COMPENSABLE; read-only is AUTO."""
        if self.allow_transfers:
            return ReversibilityLevel.COMPENSABLE
        return ReversibilityLevel.AUTO

    def __str__(self) -> str:
        """Visible - banking context should be in prompts."""
        return self._build_description(self.allow_transfers)

    # === Configuration ===

    def configure(
        self,
        capabilities: ProviderCapabilities | None = None,
    ) -> ProviderBinding:
        """Return binding with custom banking tools.

        Uses abstract trust_level and require_confirmation - providers translate:
        - Claude: trust_level -> permission_mode, auto-generates MCP server name
        - OpenAI: trust_level -> guardrails configuration
        """
        description = self._build_description(self.allow_transfers)
        custom_tools = self._build_tools(self.allow_transfers)

        # Translate transfer capability to abstract trust level
        # Transfers enabled -> elevated trust (auto-approve most actions)
        # Read-only -> standard trust
        trust = "elevated" if self.allow_transfers else "standard"

        # Require confirmation for transfer tool if enabled
        confirmations = frozenset({"BankTransfer"}) if self.allow_transfers else frozenset()

        return ProviderBinding(
            context_id=self.context_id,
            context_type="BankingContext",
            context_description=description,
            custom_tools=tuple(custom_tools),
            validate_tool=self._make_validator(self.allow_transfers),
            trust_level=trust,
            require_confirmation=confirmations,
        )

    def _build_description(self, allow_transfers: bool) -> str:
        """Build context description."""
        lines = [
            f"Banking account: {self.account_name or self.account_id}",
            f"Currency: {self.currency}",
        ]
        if allow_transfers:
            lines.append(f"Transfer limit: {self.transfer_limit:,.2f} {self.currency}")
            lines.append("Transfers ENABLED - use BankTransfer tool")
        else:
            lines.append("Read-only access (transfers disabled)")
        return "\n".join(lines)

    def _build_tools(self, allow_transfers: bool) -> list[ToolDefinition]:
        """Build custom banking tools."""
        tools = [
            ToolDefinition(
                name="BankBalance",
                description="Query the current account balance",
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "account_id": {
                            "type": "string",
                            "description": "Account ID to query (optional)",
                        },
                    },
                },
                handler=self._handle_balance_query,
            ),
        ]

        if allow_transfers:
            tools.append(
                ToolDefinition(
                    name="BankTransfer",
                    description=(f"Transfer funds (limit: {self.transfer_limit:,.2f} {self.currency})"),
                    parameters_schema={
                        "type": "object",
                        "properties": {
                            "to_account": {
                                "type": "string",
                                "description": "Destination account ID",
                            },
                            "amount": {
                                "type": "number",
                                "description": "Amount to transfer",
                            },
                            "reference": {
                                "type": "string",
                                "description": "Transfer reference/memo",
                            },
                        },
                        "required": ["to_account", "amount"],
                    },
                    handler=self._handle_transfer,
                )
            )

        return tools

    def _handle_balance_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle balance query (mock implementation)."""
        account = params.get("account_id", self.account_id)
        return {"account": account, "balance": 50000.00, "currency": self.currency}

    def _handle_transfer(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle transfer (mock implementation)."""
        return {
            "status": "pending",
            "to_account": params["to_account"],
            "amount": params["amount"],
        }

    def _make_validator(self, allow_transfers: bool) -> Callable[[ToolCall], ValidationResult]:
        """Create validator for banking tools."""

        def validate(tool: ToolCall) -> ValidationResult:
            if tool.name == "BankTransfer":
                if not allow_transfers:
                    return ValidationResult.reject(tool, "Transfers not allowed on this binding")

                amount = tool.params.get("amount", 0)
                if amount > self.transfer_limit:
                    return ValidationResult.reject(
                        tool,
                        f"Amount {amount} exceeds limit {self.transfer_limit}",
                    )
                if amount <= 0:
                    return ValidationResult.reject(tool, "Transfer amount must be positive")

            return ValidationResult.allow(tool)

        return validate

    # === v2 API ===

    def extract_effects(
        self,
        sandbox: Sandbox | None,
        result: ExecutionResult,
    ) -> Sequence[Effect]:
        """Extract banking operations as effects from tool calls.

        Parses the execution result to identify BankBalance and BankTransfer
        tool calls, emitting corresponding audit effects.

        Args:
            sandbox: Not used (banking has no filesystem operations)
            result: Execution result containing tool calls

        Returns:
            Sequence of TransferInitiated and BalanceQueried effects
        """
        effects: list[Effect] = []
        seen_accounts: set[str] = set()
        seen_transfers: set[tuple[str, float, str]] = set()

        for call, res in zip(result.tool_calls, result.tool_results, strict=False):
            if not res.success:
                continue

            if call.name == "BankBalance":
                account = call.params.get("account_id", self.account_id)
                if account not in seen_accounts:
                    seen_accounts.add(account)
                    effects.append(
                        BalanceQueried(
                            account_id=account,
                            context_id=self.context_id,
                        )
                    )

            elif call.name == "BankTransfer":
                to_account = call.params.get("to_account", "")
                amount = call.params.get("amount", 0.0)
                reference = call.params.get("reference", "")
                transfer_key = (to_account, amount, reference)

                if transfer_key not in seen_transfers:
                    seen_transfers.add(transfer_key)
                    effects.append(
                        TransferInitiated(
                            from_account=self.account_id,
                            to_account=to_account,
                            amount=amount,
                            currency=self.currency,
                            reference=reference,
                            context_id=self.context_id,
                        )
                    )

        return effects

    def apply_effect(self, effect: Effect) -> Self:
        """Apply effect to derive new context state.

        BankingContext configuration doesn't change based on operations -
        TransferInitiated and BalanceQueried are audit effects only.

        Args:
            effect: The effect to apply

        Returns:
            Self unchanged (audit effects don't modify context config)
        """
        return self


__all__ = ["BankingContext"]
