"""Prime Intellect sandbox integration with checkpoint/revert support.

Usage:
    from shepherd_sandboxes.prime import PrimeSandbox

    sandbox = PrimeSandbox(image="python:3.11-slim")
    sandbox.start()
    result = sandbox.exec("echo hello")
    sandbox.checkpoint("step1")
    sandbox.exec("rm -rf /app")
    sandbox.revert("step1")
    sandbox.stop()
"""

from shepherd_sandboxes.prime.sandbox import PrimeSandbox

__all__ = ["PrimeSandbox"]
