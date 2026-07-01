"""Tests for hardened runtime task-runner owner paths."""

from __future__ import annotations

import importlib

import pytest
from shepherd_runtime.device.container.task_runner import (
    main as runtime_main,
)
from shepherd_runtime.device.container.task_runner import (
    run_task as runtime_run_task,
)


def test_runtime_task_runner_owner_path_installs_runtime_entrypoints() -> None:
    assert runtime_run_task.__module__ == "shepherd_runtime.device.container.task_runner"
    assert runtime_main.__module__ == "shepherd_runtime.device.container.task_runner"


def test_core_task_runner_module_removed() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shepherd_core.device.container.task_runner")
