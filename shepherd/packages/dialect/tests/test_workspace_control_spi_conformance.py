from __future__ import annotations

import pytest
from vcs_core.spi.testing import assert_substrate_driver_conformant

from shepherd_dialect.workspace_control.drivers import (
    ShepherdRunLedgerDriver,
    ShepherdTaskArtifactDriver,
    ShepherdTaskLedgerDriver,
)


@pytest.mark.parametrize(
    "driver",
    [
        pytest.param(ShepherdTaskLedgerDriver(), id="ShepherdTaskLedgerDriver"),
        pytest.param(ShepherdTaskArtifactDriver(), id="ShepherdTaskArtifactDriver"),
        pytest.param(ShepherdRunLedgerDriver(), id="ShepherdRunLedgerDriver"),
    ],
)
def test_workspace_control_ledger_drivers_are_spi_conformant(driver: object) -> None:
    assert_substrate_driver_conformant(driver)
