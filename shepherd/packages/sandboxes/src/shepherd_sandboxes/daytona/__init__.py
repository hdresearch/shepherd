"""Daytona sandbox integration with OverlayFS checkpoint/revert.

Provides the same fork/revert semantics as the local container device
but backed by Daytona's remote sandboxes.

Usage:
    from shepherd_sandboxes.daytona import DaytonaSandbox

    # Spin up a remote sandbox
    sandbox = DaytonaSandbox(image="ubuntu:22.04")
    await sandbox.start()

    # Execute commands
    result = await sandbox.exec("echo hello")

    # Checkpoint / revert (OverlayFS inside the sandbox)
    await sandbox.checkpoint("before-risky-change")
    await sandbox.exec("rm -rf /app/config")
    await sandbox.revert("before-risky-change")  # restored!

    # Cleanup
    await sandbox.stop()

    # Or wrap an existing Daytona sandbox
    sandbox = DaytonaSandbox.from_existing(sandbox_id="abc123")
"""

from shepherd_sandboxes.daytona.sandbox import DaytonaSandbox

__all__ = ["DaytonaSandbox"]
