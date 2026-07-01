"""Tests for transform-owned task transformation entrypoints."""

from __future__ import annotations

from shepherd_transform.chaining import ChainResult, TransformationEngine
from shepherd_transform.source import (
    ReconstructionError,
    ReconstructionResult,
    extract_task_source,
    reconstruct_task,
    reconstruct_task_class,
    try_reconstruct_task,
    try_reconstruct_task_class,
)
from shepherd_transform.transform_lock import TaskTransformLock, TransformLock


def test_transform_chaining_owner_paths_expose_transform_symbols() -> None:
    assert ChainResult.__module__ == "shepherd_transform.chaining"
    assert TransformationEngine.__module__ == "shepherd_transform.chaining"


def test_transform_lock_owner_paths_expose_transform_symbols() -> None:
    assert TaskTransformLock.__module__ == "shepherd_transform.transform_lock"
    assert TransformLock.__module__ == "shepherd_transform.transform_lock"


def test_transform_source_owner_paths_expose_transform_symbols() -> None:
    assert ReconstructionError.__module__ == "shepherd_transform.source"
    assert ReconstructionResult.__module__ == "shepherd_transform.source"
    assert extract_task_source.__module__ == "shepherd_transform.source"
    assert reconstruct_task.__module__ == "shepherd_transform.source"
    assert reconstruct_task_class.__module__ == "shepherd_transform.source"
    assert try_reconstruct_task.__module__ == "shepherd_transform.source"
    assert try_reconstruct_task_class.__module__ == "shepherd_transform.source"
