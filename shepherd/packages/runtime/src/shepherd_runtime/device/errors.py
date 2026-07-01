"""Runtime-owned device boundary error types."""

from __future__ import annotations


class DeviceBoundaryError(Exception):
    """Base exception for device boundary operations."""


class PatchApplicationError(DeviceBoundaryError):
    """Failed to apply patches in the device."""

    def __init__(
        self,
        message: str,
        *,
        patch_name: str | None = None,
        git_output: str | None = None,
        applied_patches: list[str] | None = None,
    ):
        super().__init__(message)
        self.patch_name = patch_name
        self.git_output = git_output
        self.applied_patches = applied_patches or []

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.patch_name:
            parts.append(f"Patch: {self.patch_name}")
        if self.git_output:
            parts.append(f"Git output: {self.git_output}")
        if self.applied_patches:
            parts.append(f"Applied before failure: {', '.join(self.applied_patches)}")
        return "\n".join(parts)


class ContainerStartupError(DeviceBoundaryError):
    """Container failed to start."""

    def __init__(
        self,
        message: str,
        *,
        image: str | None = None,
        container_name: str | None = None,
        runtime_output: str | None = None,
    ):
        super().__init__(message)
        self.image = image
        self.container_name = container_name
        self.runtime_output = runtime_output

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.image:
            parts.append(f"Image: {self.image}")
        if self.container_name:
            parts.append(f"Container: {self.container_name}")
        if self.runtime_output:
            parts.append(f"Runtime output: {self.runtime_output}")
        return "\n".join(parts)


class MountError(DeviceBoundaryError):
    """Failed to create mount in device."""

    def __init__(
        self,
        message: str,
        *,
        host_path: str | None = None,
        container_path: str | None = None,
        mount_type: str | None = None,
    ):
        super().__init__(message)
        self.host_path = host_path
        self.container_path = container_path
        self.mount_type = mount_type

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.host_path and self.container_path:
            parts.append(f"Mount: {self.host_path} -> {self.container_path}")
        if self.mount_type:
            parts.append(f"Type: {self.mount_type}")
        return "\n".join(parts)


class TaskTimeoutError(DeviceBoundaryError):
    """Task execution timed out."""

    def __init__(
        self,
        message: str,
        *,
        timeout_seconds: float | None = None,
        task_id: str | None = None,
    ):
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.task_id = task_id

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.timeout_seconds is not None:
            parts.append(f"Timeout: {self.timeout_seconds}s")
        if self.task_id:
            parts.append(f"Task: {self.task_id}")
        return "\n".join(parts)


class DeviceSpaceError(DeviceBoundaryError):
    """Overlay tmpfs ran out of space."""

    def __init__(
        self,
        message: str,
        *,
        tmpfs_size: str | None = None,
        attempted_path: str | None = None,
        suggestion: str | None = None,
    ):
        super().__init__(message)
        self.tmpfs_size = tmpfs_size
        self.attempted_path = attempted_path
        self.suggestion = suggestion

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.tmpfs_size:
            parts.append(f"Tmpfs size: {self.tmpfs_size}")
        if self.attempted_path:
            parts.append(f"Failed write: {self.attempted_path}")
        if self.suggestion:
            parts.append(f"Suggestion: {self.suggestion}")
        return "\n".join(parts)


class DeviceNestingError(DeviceBoundaryError):
    """Attempted to nest device contexts."""

    def __init__(
        self,
        message: str | None = None,
        *,
        outer_device: str | None = None,
        inner_device: str | None = None,
    ):
        if message is None and outer_device and inner_device:
            message = (
                f"Cannot nest Device('{inner_device}') inside Device('{outer_device}'). "
                "Exit the current Device context first."
            )
        elif message is None:
            message = "Cannot nest device contexts. Exit the current Device context first."

        super().__init__(message)
        self.outer_device = outer_device
        self.inner_device = inner_device


class BundleApplicationError(DeviceBoundaryError):
    """Failed to apply a transfer bundle inside the device."""

    def __init__(
        self,
        message: str,
        *,
        bundle_keys: list[str] | None = None,
        failed_step: str | None = None,
    ):
        super().__init__(message)
        self.bundle_keys = bundle_keys or []
        self.failed_step = failed_step

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.failed_step:
            parts.append(f"Failed step: {self.failed_step}")
        if self.bundle_keys:
            parts.append(f"Bundle keys: {', '.join(self.bundle_keys)}")
        return "\n".join(parts)


class EffectExtractionError(DeviceBoundaryError):
    """Failed to extract effects after device execution."""

    def __init__(
        self,
        message: str,
        *,
        sandbox_id: str | None = None,
        extraction_phase: str | None = None,
    ):
        super().__init__(message)
        self.sandbox_id = sandbox_id
        self.extraction_phase = extraction_phase

    def __str__(self) -> str:
        parts = [super().__str__()]
        if self.sandbox_id:
            parts.append(f"Sandbox: {self.sandbox_id}")
        if self.extraction_phase:
            parts.append(f"Phase: {self.extraction_phase}")
        return "\n".join(parts)


__all__ = [
    "BundleApplicationError",
    "ContainerStartupError",
    "DeviceBoundaryError",
    "DeviceNestingError",
    "DeviceSpaceError",
    "EffectExtractionError",
    "MountError",
    "PatchApplicationError",
    "TaskTimeoutError",
]
