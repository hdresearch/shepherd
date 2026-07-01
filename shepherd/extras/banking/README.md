# shepherd-banking

Banking domain package for the Shepherd framework.

## Overview

This package provides banking-specific functionality for AI agents:

- **BankingContext**: Financial operations context with transfer controls
- **Tasks**: Pre-built tasks for transfers and balance queries
- **Effects**: Domain-specific effects for audit trails

## Installation

```bash
pip install shepherd-banking
```

## Quick Start

```python
import asyncio

from shepherd import handle, workspace
from shepherd_core.schema import SINGLE_OUTPUT_KEY
from shepherd_runtime.provider_boundary import ModelResponse
from shepherd_banking import BankingContext, query_balance


async def main() -> None:
    banking = BankingContext(
        account_id="ACC-001",
        account_name="Operations Account",
        allow_transfers=True,
        transfer_limit=10000.0,
    )

    def fake_model(request):
        return ModelResponse(
            structured_output={
                SINGLE_OUTPUT_KEY: {
                    "balance": 1250.0,
                    "currency": "USD",
                    "account_name": "Operations Account",
                }
            }
        )

    with workspace(model="offline-banking") as ws, handle("model.call", fake_model):
        ws.scope.bind(banking)
        result = await query_balance(account_id="ACC-001")

    print(f"Balance: {result.balance}")


asyncio.run(main())
```

## BankingContext

The `BankingContext` provides:

- **Read-only mode**: Query balances without transfer capability
- **Transfer mode**: Enable transfers with configurable limits
- **COMPENSABLE reversibility**: Transfers require compensation to reverse
- **Custom tools**: `BankBalance` and `BankTransfer` tools

```python
# Read-only context
readonly_banking = BankingContext(
    account_id="ACC-001",
    allow_transfers=False,  # Default
)

# Transfer-enabled context
transfer_banking = BankingContext(
    account_id="ACC-001",
    allow_transfers=True,
    transfer_limit=5000.0,
    currency="EUR",
)
```

## Effects

Banking operations emit domain-specific effects:

- `TransferInitiated`: When a transfer is requested
- `TransferCompleted`: When a transfer succeeds
- `TransferFailed`: When a transfer fails
- `BalanceQueried`: When a balance is checked

```python
from shepherd_banking import TransferInitiated, BalanceQueried

# Query effects from the stream
for effect in scope.effects.query(TransferInitiated):
    print(f"Transfer: {effect.amount} {effect.currency} to {effect.to_account}")
```

## License

MIT
