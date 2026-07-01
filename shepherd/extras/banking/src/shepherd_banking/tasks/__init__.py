"""Banking domain tasks.

Pre-built function-form tasks for common banking operations:

- ``transfer_funds``: Initiate a bank transfer
- ``query_balance``: Query account balance

Each task returns a frozen-dataclass result type
(``TransferResult``, ``BalanceResult``).
"""

from .query import BalanceResult, query_balance
from .transfer import TransferResult, transfer_funds

__all__ = [
    "BalanceResult",
    "TransferResult",
    "query_balance",
    "transfer_funds",
]
