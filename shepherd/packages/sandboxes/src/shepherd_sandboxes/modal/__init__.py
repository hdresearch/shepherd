"""Modal sandbox integration with filesystem snapshot checkpoint/revert.

Modal uses gVisor which blocks OverlayFS. Uses Modal's native
snapshot_filesystem() API instead — slower but functional.

Usage:
    from shepherd_sandboxes.modal import ModalSandbox

    sandbox = ModalSandbox(image="python:3.11-slim")
    sandbox.start()
    sandbox.exec("echo hello")
    sandbox.checkpoint("step1")
    sandbox.exec("rm -rf /app")
    sandbox.revert("step1")  # creates new sandbox from snapshot
    sandbox.stop()
"""

from shepherd_sandboxes.modal.sandbox import ModalSandbox

__all__ = ["ModalSandbox"]
