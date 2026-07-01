"""Shared workspace-control ledger constants and keyed-record stores."""

from __future__ import annotations

from vcs_core.keyed_json_tree import KeyedJsonTreeStore

TASK_LEDGER_BINDING = "shepherd.tasks"
TASK_ARTIFACT_BINDING = "shepherd.task-artifacts"
RUN_LEDGER_BINDING = "shepherd.runs"

TASK_LEDGER_SCHEMA = "shepherd.workspace_control.tasks.v2"
TASK_ARTIFACT_SCHEMA = "shepherd.workspace_control.task_artifact.v1"
RUN_LEDGER_SCHEMA = "shepherd.workspace_control.runs.v2"
RUN_LEDGER_STORAGE_SHAPE = "keyed-json-tree"

RUN_RECORDS = KeyedJsonTreeStore("runs/by-ref")
RUN_ARGS = KeyedJsonTreeStore("args/by-ref")
FLOWS = KeyedJsonTreeStore("flows/by-id")
FLOW_RUNS = KeyedJsonTreeStore("flow-runs/by-run")
