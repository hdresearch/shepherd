"""Positive capabilities-as-runtime-contract test (SPI v0.1 Phase A.2).

The negative invariant — "don't put aspirational entries in
``capabilities.accepts``" — is documented in SPI doc §Result Shape
"Capabilities are a runtime contract" and tested implicitly by
``test_reduce_request_not_accepted_pending_t2_wiring`` (workspace driver
deliberately omits ``ReduceRequest`` until T2 wires the handler).

This test is the **symmetric positive complement**: for every type in
each driver's ``capabilities.accepts``, dispatching a minimal valid
request of that type via the driver's typed ``prepare`` must produce a
structurally valid ``DriverIngressResult`` rather than an
``UnsupportedRequestError`` or ``NotImplementedError``.

Since SP-substrate-authoring (2026-06-12) the per-variant dispatch logic
lives in the exportable conformance kit (``vcs_core.spi.testing``); this
file consumes it so the built-ins and out-of-tree drivers are held to one
source of truth (``decisions.md`` ``substrate-conformance-kit``). Adding a
new built-in driver to ``DRIVERS_UNDER_TEST`` opts it into the kit's
checks automatically.
"""

from __future__ import annotations

import pytest
from vcs_core.spi.testing import assert_substrate_driver_conformant

# Shared inventory of drivers under contract test (tests/contract/conftest.py).
# The inventory-drift guard below fires when DRIVERS_UNDER_TEST drifts from the
# expected built-in set.
from tests.contract.conftest import DRIVERS_UNDER_TEST


@pytest.mark.parametrize("driver_cls", DRIVERS_UNDER_TEST, ids=lambda cls: cls.__name__)
def test_advertised_capability_dispatches_without_contract_violation(driver_cls: type) -> None:
    """For every type in capabilities.accepts, prepare() must not raise a contract
    violation (``UnsupportedRequestError`` / ``NotImplementedError``).

    The kit's aggregate runs the per-variant dispatch check across the driver's
    whole accepted set (plus the structural / identity / describe-coherence /
    evidence-kind / execution-negotiation checks); this contract test delegates
    to it rather than re-deriving per-variant request factories here.
    """
    assert_substrate_driver_conformant(driver_cls())


def test_drivers_under_test_covers_known_built_in_drivers() -> None:
    """Guard against accidentally dropping a driver from coverage.

    When a new built-in driver is added to ``_world_substrate_adapters.py``,
    it must be added to ``DRIVERS_UNDER_TEST`` above so its accepted variants
    are positively tested. This guard fails when the inventory drifts.
    """
    expected_driver_names = {
        "WorkspaceSubstrateDriver",
        "SessionStateSubstrateDriver",
        "TaskTraceSubstrateDriver",
        "WorldRefSubstrateDriver",
    }
    covered = {cls.__name__ for cls in DRIVERS_UNDER_TEST}
    assert covered == expected_driver_names, (
        f"drivers under test {covered} drifted from expected built-in inventory "
        f"{expected_driver_names}; update DRIVERS_UNDER_TEST so the conformance "
        f"kit covers any new driver's accepted variants."
    )
