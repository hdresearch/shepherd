"""The CLI driver-plugin registration (B4b slice 3 W3).

The B3c-3 W8 signpost discharged: `mg exec runtime run` worked per-call with
in-process driver binding; this registration makes the pairing durable at the
process level — vcs-core's plugin discovery (`vcscore.substrate_plugins` entry
points) finds the dialect's run driver, so a repo can bind it by name
(`vcs-core binding add runtime shepherd.run_driver`) and drive runs from the
CLI without Python composition. The trace driver needs no registration here —
`shepherd.task_trace` is vcs-core-builtin.

Import discipline holds: `vcs_core.manifest` is public packaging surface.
"""

from __future__ import annotations

from vcs_core.manifest import SubstrateManifest, SubstratePlugin

RUN_DRIVER_PLUGIN = SubstratePlugin(
    name="shepherd.run_driver",
    substrate=("shepherd_dialect.run_driver", "ShepherdRunDriver"),
    manifest=SubstrateManifest(
        name="shepherd.run_driver",
        description="The Shepherd dialect's run driver (execution-bound; composes vcs-core's verbs)",
        tier="explicit",
    ),
    implementation_kind="driver",
)

TASK_LEDGER_PLUGIN = SubstratePlugin(
    name="shepherd.task_ledger",
    substrate=("shepherd_dialect.workspace_control.drivers", "ShepherdTaskLedgerDriver"),
    manifest=SubstrateManifest(
        name="shepherd.task_ledger",
        description="The Shepherd task-library ledger substrate",
        tier="explicit",
    ),
    implementation_kind="driver",
)

TASK_ARTIFACT_PLUGIN = SubstratePlugin(
    name="shepherd.task_artifacts",
    substrate=("shepherd_dialect.workspace_control.drivers", "ShepherdTaskArtifactDriver"),
    manifest=SubstrateManifest(
        name="shepherd.task_artifacts",
        description="The Shepherd immutable task-artifact substrate",
        tier="explicit",
    ),
    implementation_kind="driver",
)

RUN_LEDGER_PLUGIN = SubstratePlugin(
    name="shepherd.run_ledger",
    substrate=("shepherd_dialect.workspace_control.drivers", "ShepherdRunLedgerDriver"),
    manifest=SubstrateManifest(
        name="shepherd.run_ledger",
        description="The Shepherd run-control ledger substrate",
        tier="explicit",
    ),
    implementation_kind="driver",
)
