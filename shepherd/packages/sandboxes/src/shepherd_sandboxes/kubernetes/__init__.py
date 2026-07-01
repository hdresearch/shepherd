"""Kubernetes sandbox integration with OverlayFS checkpoint/revert.

Usage:
    from shepherd_sandboxes.kubernetes import K8sSandbox

    sandbox = K8sSandbox(image="ubuntu:22.04", kubeconfig="/path/to/kubeconfig")
    sandbox.start()
    sandbox.exec("echo hello")
    sandbox.checkpoint("step1")
    sandbox.exec("rm -rf /app")
    sandbox.revert("step1")
    sandbox.stop()
"""

from shepherd_sandboxes.kubernetes.sandbox import K8sSandbox

__all__ = ["K8sSandbox"]
