"""Default v2 world-storage installation for production vcs-core repos."""

from __future__ import annotations

from pathlib import Path

from vcs_core._world_storage_manager import SubstrateStoreSpec, WorldStorageManager
from vcs_core._world_types import SubstrateStoreIdentity

DEFAULT_WORLD_STORAGE_ROOT = "world-vectors"
DEFAULT_WORLD_STORE_ID = "store_world_main"
DEFAULT_WORKSPACE_STORE_ID = "store_workspace"
DEFAULT_WORKSPACE_STORE_LOCATOR = "substrates/workspace.git"
DEFAULT_WORKSPACE_RESOURCE_ID = "fs:repo-main"
DEFAULT_TRACE_STORE_ID = "store_trace"
DEFAULT_TRACE_STORE_LOCATOR = "substrates/trace.git"
DEFAULT_TRACE_RESOURCE_ID = "shepherd-trace:main"
DEFAULT_SHEPHERD_TASKS_STORE_ID = "store_shepherd_tasks"
DEFAULT_SHEPHERD_TASKS_STORE_LOCATOR = "substrates/shepherd-tasks.git"
DEFAULT_SHEPHERD_TASKS_RESOURCE_ID = "shepherd-tasks:main"
DEFAULT_SHEPHERD_TASK_ARTIFACTS_STORE_ID = "store_shepherd_task_artifacts"
DEFAULT_SHEPHERD_TASK_ARTIFACTS_STORE_LOCATOR = "substrates/shepherd-task-artifacts.git"
DEFAULT_SHEPHERD_TASK_ARTIFACTS_RESOURCE_ID = "shepherd-task-artifacts:main"
DEFAULT_SHEPHERD_RUNS_STORE_ID = "store_shepherd_runs"
DEFAULT_SHEPHERD_RUNS_STORE_LOCATOR = "substrates/shepherd-runs.git"
DEFAULT_SHEPHERD_RUNS_RESOURCE_ID = "shepherd-runs:main"


def default_world_storage_root(repo_path: str | Path) -> Path:
    """Return the install-local root for v2 world-vector storage."""
    return Path(repo_path) / DEFAULT_WORLD_STORAGE_ROOT


def default_world_storage_exists(repo_path: str | Path) -> bool:
    """Return true when the default v2 world-storage installation exists."""
    return (default_world_storage_root(repo_path) / "world-stores.json").exists()


def default_world_storage_specs() -> tuple[SubstrateStoreSpec, ...]:
    """Return the default production substrate stores for a vcs-core repo.

    ``store_trace`` joined the defaults at B4b slice 1 (W2): fresh installs are
    trace-capable. ``store_shepherd_tasks``, ``store_shepherd_task_artifacts``,
    and ``store_shepherd_runs`` joined at the workspace-control core-loop slices
    so task/run ledgers and immutable task artifacts can use the same
    selectable-driver publication path. The installation store set is pinned at
    init with no upgrade path (S1 finding,
    `spikes/260610-b4b-s1-selectable-route/`), so pre-existing installations
    fail loud on open and re-initialize — the sanctioned pre-launch posture
    (zero users; an `add_substrate_store` upgrade helper is deliberately
    post-launch work).
    """
    return (
        SubstrateStoreSpec(
            identity=SubstrateStoreIdentity(
                store_id=DEFAULT_WORKSPACE_STORE_ID,
                kind="filesystem",
                resource_id=DEFAULT_WORKSPACE_RESOURCE_ID,
            ),
            locator=DEFAULT_WORKSPACE_STORE_LOCATOR,
        ),
        SubstrateStoreSpec(
            identity=SubstrateStoreIdentity(
                store_id=DEFAULT_TRACE_STORE_ID,
                kind="shepherd.trace",
                resource_id=DEFAULT_TRACE_RESOURCE_ID,
            ),
            locator=DEFAULT_TRACE_STORE_LOCATOR,
        ),
        SubstrateStoreSpec(
            identity=SubstrateStoreIdentity(
                store_id=DEFAULT_SHEPHERD_TASKS_STORE_ID,
                kind="shepherd.tasks",
                resource_id=DEFAULT_SHEPHERD_TASKS_RESOURCE_ID,
            ),
            locator=DEFAULT_SHEPHERD_TASKS_STORE_LOCATOR,
        ),
        SubstrateStoreSpec(
            identity=SubstrateStoreIdentity(
                store_id=DEFAULT_SHEPHERD_TASK_ARTIFACTS_STORE_ID,
                kind="shepherd.task-artifacts",
                resource_id=DEFAULT_SHEPHERD_TASK_ARTIFACTS_RESOURCE_ID,
            ),
            locator=DEFAULT_SHEPHERD_TASK_ARTIFACTS_STORE_LOCATOR,
        ),
        SubstrateStoreSpec(
            identity=SubstrateStoreIdentity(
                store_id=DEFAULT_SHEPHERD_RUNS_STORE_ID,
                kind="shepherd.runs",
                resource_id=DEFAULT_SHEPHERD_RUNS_RESOURCE_ID,
            ),
            locator=DEFAULT_SHEPHERD_RUNS_STORE_LOCATOR,
        ),
    )


def open_or_init_default_world_storage(repo_path: str | Path) -> WorldStorageManager:
    """Open or initialize the default v2 world-storage installation.

    Substrate stores are configured with alternates pointing at the scalar
    vcs-core store at ``repo_path``: tree-backed workspace revisions reference
    Git tree/blob oids materialized by the scalar capture path, so the
    substrate's ODB needs to see those objects.
    """
    return WorldStorageManager.open_or_init(
        default_world_storage_root(repo_path),
        world_store_id=DEFAULT_WORLD_STORE_ID,
        stores=default_world_storage_specs(),
        substrate_shared_object_repo_path=repo_path,
    )


def open_existing_default_world_storage(repo_path: str | Path) -> WorldStorageManager:
    """Open the default v2 world-storage installation without creating it.

    See :func:`open_or_init_default_world_storage` for the alternates contract.
    """
    return WorldStorageManager.open_existing(
        default_world_storage_root(repo_path),
        world_store_id=DEFAULT_WORLD_STORE_ID,
        stores=default_world_storage_specs(),
        substrate_shared_object_repo_path=repo_path,
    )
