"""Banking domain effects.

These effects capture financial operations for audit and replay:
- TransferInitiated: A transfer was requested
- TransferCompleted: A transfer succeeded
- TransferFailed: A transfer failed
- BalanceQueried: A balance was checked

All effects are frozen Pydantic models with effect_type discriminator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from shepherd_core import Effect

if TYPE_CHECKING:
    from collections.abc import Mapping


class TransferInitiated(Effect):
    """A bank transfer was initiated.

    This effect is emitted when a transfer is requested through the
    BankingContext. The transfer may still be pending approval or
    processing.

    Attributes:
        from_account: Source account ID
        to_account: Destination account ID
        amount: Transfer amount
        currency: Currency code (e.g., "USD", "EUR")
        reference: Transfer reference/memo
    """

    effect_type: Literal["transfer_initiated"] = "transfer_initiated"

    from_account: str = ""
    to_account: str = ""
    amount: float = 0.0
    currency: str = "USD"
    reference: str = ""


class TransferCompleted(Effect):
    """A bank transfer completed successfully.

    Attributes:
        from_account: Source account ID
        to_account: Destination account ID
        amount: Transfer amount
        currency: Currency code
        reference: Transfer reference/memo
        transaction_id: Bank's transaction identifier
    """

    effect_type: Literal["transfer_completed"] = "transfer_completed"

    from_account: str = ""
    to_account: str = ""
    amount: float = 0.0
    currency: str = "USD"
    reference: str = ""
    transaction_id: str = ""


class TransferFailed(Effect):
    """A bank transfer failed.

    Attributes:
        from_account: Source account ID
        to_account: Destination account ID
        amount: Transfer amount
        currency: Currency code
        reason: Failure reason
    """

    effect_type: Literal["transfer_failed"] = "transfer_failed"

    from_account: str = ""
    to_account: str = ""
    amount: float = 0.0
    currency: str = "USD"
    reason: str = ""


class BalanceQueried(Effect):
    """A balance query was performed.

    Attributes:
        account_id: Account that was queried
        balance: The balance returned (if available)
        currency: Currency code
    """

    effect_type: Literal["balance_queried"] = "balance_queried"

    account_id: str = ""
    balance: float | None = None
    currency: str = "USD"


def get_effect_types() -> Mapping[str, type[Effect]]:
    """Return the explicit effect contributor surface for runtime decode."""
    return {
        "balance_queried": BalanceQueried,
        "transfer_completed": TransferCompleted,
        "transfer_failed": TransferFailed,
        "transfer_initiated": TransferInitiated,
    }


__all__ = [
    "BalanceQueried",
    "TransferCompleted",
    "TransferFailed",
    "TransferInitiated",
    "get_effect_types",
]
