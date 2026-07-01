"""Runtime-owned syntax nucleus foundation."""

from __future__ import annotations

from .artifacts import Artifact, emit_artifact
from .callable_task import (
    CallableTask,
    ParameterMetadata,
    StructuralMay,
    TaskMetadata,
    extract_callable_task_metadata,
    task,
)
from .delivery import deliver
from .handles import GitRepo, GitRepoBasis
from .profiles import EffectSurfaceProfile, Permissive, ReadOnly
from .task_hooks import TaskExecutionHook, install_task_execution_hook
from .types import (
    RUN_REF_SCHEMA,
    DeliveryException,
    DeliveryExhausted,
    DeliveryFailed,
    DeliveryLimits,
    DeliveryStopped,
    Exhausted,
    Failed,
    Finished,
    NoActiveTaskRun,
    Run,
    RunInProgress,
    RunRef,
    Stopped,
    WorkspaceAlreadyConfigured,
    WorkspaceNotConfigured,
)
from .workspace import Workspace, current_workspace, reset_workspace_for_tests, workspace

__all__ = [
    "RUN_REF_SCHEMA",
    "Artifact",
    "CallableTask",
    "DeliveryException",
    "DeliveryExhausted",
    "DeliveryFailed",
    "DeliveryLimits",
    "DeliveryStopped",
    "EffectSurfaceProfile",
    "Exhausted",
    "Failed",
    "Finished",
    "GitRepo",
    "GitRepoBasis",
    "NoActiveTaskRun",
    "ParameterMetadata",
    "Permissive",
    "ReadOnly",
    "Run",
    "RunInProgress",
    "RunRef",
    "Stopped",
    "StructuralMay",
    "TaskExecutionHook",
    "TaskMetadata",
    "Workspace",
    "WorkspaceAlreadyConfigured",
    "WorkspaceNotConfigured",
    "current_workspace",
    "deliver",
    "emit_artifact",
    "extract_callable_task_metadata",
    "install_task_execution_hook",
    "reset_workspace_for_tests",
    "task",
    "workspace",
]
