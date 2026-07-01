"""Thread-safe task-transformation locking for concurrent meta-task operations.

This module provides infrastructure for safely transforming tasks when multiple
transforms may be attempted concurrently. It ensures:

- Only one transformation per task at a time (exclusive locking)
- Execution sees consistent snapshots during transformation
- Crashed transformations are recovered via lock expiration
- Atomic commit-or-rollback semantics

Key Components:
- TransformLock: Lock state for a specific task transformation
- TaskTransformLock: Thread-safe registry with transformation locking
- LockError: Raised when lock cannot be acquired

Example:
    >>> from shepherd_transform.transform_lock import TaskTransformLock, LockError
    >>>
    >>> registry = TaskTransformLock()
    >>> registry.register(MyTask, "class MyTask...")
    >>>
    >>> # Safe transformation with context manager
    >>> with registry.transform_context("MyTask") as holder_id:
    ...     # Do transformation work (e.g., call LLM)
    ...     new_source = transform_with_llm(registry.get_source("MyTask"))
    ...     new_class = reconstruct_task_class(new_source)
    ...     registry.commit_transform("MyTask", holder_id, new_class, new_source)
    >>>
    >>> # Concurrent transform attempt would raise LockError
    >>> try:
    ...     with registry.transform_context("MyTask"):
    ...         pass  # Would fail if another transform is in progress
    ... except LockError as e:
    ...     print(f"Task is being transformed: {e}")

See Also:
    - shepherd_transform.chaining: Transformation chaining with confidence tracking
    - shepherd_transform.source: Task reconstruction utilities
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# =============================================================================
# Constants
# =============================================================================

DEFAULT_LOCK_TIMEOUT_SECONDS = 60.0
"""Default timeout for transform locks (handles crashed transformations)."""


# =============================================================================
# Exceptions
# =============================================================================


class LockError(Exception):
    """Raised when a transform lock cannot be acquired.

    This typically occurs when another transformation is already in progress
    for the same task.

    Attributes:
        task_name: Name of the task that couldn't be locked
        holder_id: ID of the current lock holder (if known)
        message: Human-readable error description
    """

    def __init__(
        self,
        message: str,
        *,
        task_name: str | None = None,
        holder_id: str | None = None,
    ) -> None:
        self.task_name = task_name
        self.holder_id = holder_id
        super().__init__(message)


class TransformError(Exception):
    """Raised when a transformation operation fails.

    This is a general error for transformation failures that aren't
    lock-related (e.g., invalid source, reconstruction failure).
    """


# =============================================================================
# Transform State
# =============================================================================


class TransformState(Enum):
    """State of a task transformation."""

    IDLE = auto()
    """No transformation in progress."""

    TRANSFORMING = auto()
    """Transformation is in progress (lock held)."""

    COMMITTING = auto()
    """Transformation complete, committing changes."""


@dataclass
class TransformLock:
    """Lock state for a specific task transformation.

    Tracks who holds the lock, when it was acquired, and whether it has
    expired (for crash recovery).

    Attributes:
        task_name: Name of the locked task
        holder_id: Unique identifier for the lock holder
        acquired_at: Unix timestamp when lock was acquired
        state: Current transformation state
        timeout_seconds: Lock timeout for crash recovery
    """

    task_name: str
    holder_id: str
    acquired_at: float
    state: TransformState = TransformState.TRANSFORMING
    timeout_seconds: float = DEFAULT_LOCK_TIMEOUT_SECONDS

    @property
    def is_expired(self) -> bool:
        """Check if this lock has expired (for crash recovery)."""
        return time.time() - self.acquired_at > self.timeout_seconds

    @property
    def held_for_seconds(self) -> float:
        """How long this lock has been held."""
        return time.time() - self.acquired_at


# =============================================================================
# Task Transform Lock Registry
# =============================================================================


class TaskTransformLock:
    """Thread-safe task registry with transformation locking.

    Provides concurrent-safe task transformation with:
    - Mutex protection for task state
    - Per-task transform locks to prevent concurrent modifications
    - Snapshot isolation for execution during transformation
    - Automatic crash recovery via lock expiration

    The registry stores both task classes and their source code, enabling
    meta-task operations that need to read and transform task definitions.

    Thread Safety:
        All public methods are thread-safe. The registry uses an RLock
        (reentrant lock) internally, so the same thread can call multiple
        methods without deadlocking.

    Example:
        >>> registry = TaskTransformLock(lock_timeout=30.0)
        >>> registry.register(MyTask, extract_task_source(MyTask))
        >>>
        >>> # Get snapshot for execution (never blocks)
        >>> task_class, source = registry.get_snapshot("MyTask")
        >>>
        >>> # Transform with exclusive lock
        >>> with registry.transform_context("MyTask") as holder_id:
        ...     new_source = llm_transform(source)
        ...     new_class = reconstruct_task_class(new_source)
        ...     registry.commit_transform("MyTask", holder_id, new_class, new_source)
    """

    def __init__(self, lock_timeout: float = DEFAULT_LOCK_TIMEOUT_SECONDS) -> None:
        """Initialize the registry.

        Args:
            lock_timeout: Seconds before locks expire (for crash recovery).
                         Default is 60 seconds.
        """
        self._tasks: dict[str, tuple[type, str]] = {}  # name -> (class, source)
        self._locks: dict[str, TransformLock] = {}  # name -> lock
        self._mutex = threading.RLock()
        self._lock_timeout = lock_timeout

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register(self, task_class: type, source: str) -> None:
        """Register a task class with its source code.

        Args:
            task_class: A class decorated with @task (must have _task_meta)
            source: The source code of the task class

        Raises:
            ValueError: If task_class doesn't have _task_meta attribute
        """
        if not hasattr(task_class, "_task_meta"):
            raise ValueError(f"Class {task_class.__name__} is not a @task (missing _task_meta attribute)")

        with self._mutex:
            name = task_class._task_meta.name
            self._tasks[name] = (task_class, source)

    def unregister(self, task_name: str) -> bool:
        """Remove a task from the registry.

        Args:
            task_name: Name of the task to remove

        Returns:
            True if task was removed, False if not found
        """
        with self._mutex:
            if task_name in self._tasks:
                del self._tasks[task_name]
                # Also clean up any stale lock
                self._locks.pop(task_name, None)
                return True
            return False

    # -------------------------------------------------------------------------
    # Read Operations (never block on transforms)
    # -------------------------------------------------------------------------

    def get_task(self, task_name: str) -> type | None:
        """Get task class by name.

        This returns the current registered class, even if a transformation
        is in progress. Use get_snapshot() for consistent class+source pairs.

        Args:
            task_name: Name of the task

        Returns:
            The task class, or None if not found
        """
        with self._mutex:
            entry = self._tasks.get(task_name)
            return entry[0] if entry else None

    def get_source(self, task_name: str) -> str | None:
        """Get task source code by name.

        Args:
            task_name: Name of the task

        Returns:
            The source code string, or None if not found
        """
        with self._mutex:
            entry = self._tasks.get(task_name)
            return entry[1] if entry else None

    def get_snapshot(self, task_name: str) -> tuple[type, str] | None:
        """Get a consistent snapshot of task state.

        Returns the current (class, source) pair atomically. This provides
        read isolation - execution sees consistent state even if a
        transformation is in progress.

        Args:
            task_name: Name of the task

        Returns:
            Tuple of (task_class, source_code), or None if not found
        """
        with self._mutex:
            return self._tasks.get(task_name)

    def list_tasks(self) -> list[str]:
        """List all registered task names.

        Returns:
            List of task names
        """
        with self._mutex:
            return list(self._tasks.keys())

    def __contains__(self, task_name: str) -> bool:
        """Check if a task is registered."""
        with self._mutex:
            return task_name in self._tasks

    def __len__(self) -> int:
        """Return number of registered tasks."""
        with self._mutex:
            return len(self._tasks)

    # -------------------------------------------------------------------------
    # Lock Operations
    # -------------------------------------------------------------------------

    def try_acquire_transform_lock(
        self,
        task_name: str,
        holder_id: str | None = None,
    ) -> str:
        """Try to acquire exclusive transform lock for a task.

        Only one transformation can be in progress per task. If another
        transformation is active, this raises LockError.

        Args:
            task_name: Name of the task to lock
            holder_id: Optional identifier for the lock holder.
                      If not provided, a UUID is generated.

        Returns:
            The holder_id (use this to release or commit)

        Raises:
            LockError: If task not found or already being transformed
        """
        holder_id = holder_id or str(uuid.uuid4())

        with self._mutex:
            if task_name not in self._tasks:
                raise LockError(
                    f"Task '{task_name}' not found in registry",
                    task_name=task_name,
                )

            existing_lock = self._locks.get(task_name)

            if existing_lock is not None:
                if existing_lock.is_expired:
                    # Expired lock - clean it up (crash recovery)
                    del self._locks[task_name]
                else:
                    raise LockError(
                        f"Task '{task_name}' is being transformed by "
                        f"{existing_lock.holder_id} "
                        f"(held for {existing_lock.held_for_seconds:.1f}s)",
                        task_name=task_name,
                        holder_id=existing_lock.holder_id,
                    )

            # Acquire new lock
            self._locks[task_name] = TransformLock(
                task_name=task_name,
                holder_id=holder_id,
                acquired_at=time.time(),
                state=TransformState.TRANSFORMING,
                timeout_seconds=self._lock_timeout,
            )

            return holder_id

    def release_transform_lock(self, task_name: str, holder_id: str) -> bool:
        """Release a transform lock without committing changes.

        Use this to abandon a transformation (rollback). The task state
        remains unchanged.

        Args:
            task_name: Name of the locked task
            holder_id: The holder_id returned from try_acquire_transform_lock

        Returns:
            True if lock was released, False if not held by this holder
        """
        with self._mutex:
            existing_lock = self._locks.get(task_name)

            if existing_lock is None:
                return False

            if existing_lock.holder_id != holder_id:
                return False

            del self._locks[task_name]
            return True

    def commit_transform(
        self,
        task_name: str,
        holder_id: str,
        new_class: type,
        new_source: str,
    ) -> bool:
        """Commit a transformation atomically.

        Updates the task to the new class and source, then releases the lock.
        Only the lock holder can commit.

        Args:
            task_name: Name of the task being transformed
            holder_id: The holder_id from try_acquire_transform_lock
            new_class: The new task class
            new_source: The new source code

        Returns:
            True if committed successfully, False if lock not held
        """
        with self._mutex:
            existing_lock = self._locks.get(task_name)

            if existing_lock is None:
                return False

            if existing_lock.holder_id != holder_id:
                return False

            # Update task state
            self._tasks[task_name] = (new_class, new_source)

            # Release lock
            del self._locks[task_name]

            return True

    def is_transforming(self, task_name: str) -> bool:
        """Check if a task is currently being transformed.

        Args:
            task_name: Name of the task to check

        Returns:
            True if transformation is in progress (lock held and not expired)
        """
        with self._mutex:
            lock = self._locks.get(task_name)
            return lock is not None and not lock.is_expired

    def get_lock_info(self, task_name: str) -> TransformLock | None:
        """Get information about the current lock on a task.

        Args:
            task_name: Name of the task

        Returns:
            The TransformLock if locked, None otherwise
        """
        with self._mutex:
            lock = self._locks.get(task_name)
            if lock is not None and lock.is_expired:
                # Clean up expired lock
                del self._locks[task_name]
                return None
            return lock

    # -------------------------------------------------------------------------
    # Context Manager
    # -------------------------------------------------------------------------

    @contextmanager
    def transform_context(self, task_name: str) -> Iterator[str]:
        """Context manager for safe task transformation.

        Acquires the transform lock on entry, releases on exit (whether
        successful or not). If the context exits without commit_transform
        being called, the transformation is rolled back.

        Args:
            task_name: Name of the task to transform

        Yields:
            The holder_id (use with commit_transform)

        Raises:
            LockError: If task not found or already being transformed

        Example:
            >>> with registry.transform_context("MyTask") as holder_id:
            ...     # Transform the task
            ...     new_source = transform(registry.get_source("MyTask"))
            ...     new_class = reconstruct_task_class(new_source)
            ...     # Commit (or let context exit to rollback)
            ...     registry.commit_transform("MyTask", holder_id, new_class, new_source)
        """
        holder_id = self.try_acquire_transform_lock(task_name)
        try:
            yield holder_id
        except Exception:
            # Release lock on error (rollback)
            self.release_transform_lock(task_name, holder_id)
            raise
        finally:
            # Clean up if not committed (idempotent)
            self.release_transform_lock(task_name, holder_id)


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    # Constants
    "DEFAULT_LOCK_TIMEOUT_SECONDS",
    # Exceptions
    "LockError",
    # Main class
    "TaskTransformLock",
    "TransformError",
    "TransformLock",
    # Data classes
    "TransformState",
]
