"""E2B sandbox integration with OverlayFS checkpoint/revert.

E2B provides real Linux kernels via Firecracker. Runs as non-root
user with sudo access — all mount commands prefixed with sudo.

Usage:
    from shepherd_sandboxes.e2b import E2BSandbox

    sandbox = E2BSandbox()
    sandbox.start()
    sandbox.exec("echo hello")
    sandbox.checkpoint("step1")
    sandbox.exec("rm -rf /app")
    sandbox.revert("step1")
    sandbox.stop()
"""

from shepherd_sandboxes.e2b.sandbox import E2BSandbox

__all__ = ["E2BSandbox"]
