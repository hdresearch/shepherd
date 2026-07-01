"""Example 13: Remote Sandboxes with Checkpoint/Revert.

This tutorial demonstrates how to use remote sandbox backends with
OverlayFS checkpoint/revert. The same interface works across Daytona,
E2B, and Kubernetes — just swap the import.

Key concepts:
1. DaytonaSandbox / E2BSandbox / K8sSandbox — same interface, different backends
2. checkpoint(name) — save container state in ~150ms (OverlayFS)
3. revert(name) — restore container state in ~150ms
4. Multiple checkpoints — jump between any saved state
5. Context manager — automatic cleanup on exit

Prerequisites:
- For Daytona: `pip install daytona-sdk` + DAYTONA_API_KEY
- For E2B: `pip install e2b` + E2B_API_KEY
- For K8s: `pip install kubernetes` + KUBECONFIG

Run with:
    uv run python shepherd/examples/tutorials/13_remote_sandboxes.py --backend daytona
    uv run python shepherd/examples/tutorials/13_remote_sandboxes.py --backend e2b
    uv run python shepherd/examples/tutorials/13_remote_sandboxes.py --backend k8s

See Also:
    docs/remote/overview.md — full backend comparison
"""

from __future__ import annotations

import argparse
import sys
import time


def section(title: str) -> None:
    """Print a section header."""
    print(f"\n{'=' * 60}")
    print(title)
    print("=" * 60)


# =============================================================================
# Parse backend selection
# =============================================================================

parser = argparse.ArgumentParser(description="Remote sandbox tutorial")
parser.add_argument(
    "--backend",
    choices=["daytona", "e2b", "k8s"],
    default="e2b",
    help="Sandbox backend (default: e2b)",
)
parser.add_argument("--kubeconfig", default=None, help="Path to kubeconfig (for k8s)")
parser.add_argument("--image", default="ubuntu:22.04", help="Container image")
args = parser.parse_args()


# =============================================================================
# Create the sandbox (backend-agnostic after this point)
# =============================================================================

section(f"Remote Sandbox Tutorial ({args.backend.upper()})")

if args.backend == "daytona":
    from shepherd_sandboxes.daytona import DaytonaSandbox

    print("Creating Daytona sandbox...")
    # Daytona is async — we run the async version
    import asyncio

    async def run_daytona() -> None:
        """Run the tutorial with Daytona backend."""
        async with DaytonaSandbox(image=args.image) as sandbox:
            print(f"Sandbox: {sandbox.sandbox_id}")
            print(f"OverlayFS: {sandbox.using_overlay}")
            await run_tutorial_async(sandbox)

    async def run_tutorial_async(sandbox: DaytonaSandbox) -> None:
        """Tutorial steps for async backends (Daytona)."""
        # A: Basic exec
        section("A: Basic Execution")
        r = await sandbox.exec("echo 'Hello from Daytona!'")
        print(f"Output: {r.stdout.strip()}")
        print(f"Latency: {r.duration_ms:.0f}ms")

        # B: Checkpoint and revert
        section("B: Checkpoint & Revert")
        await sandbox.exec("echo 'original config' > /app/config.txt")
        print(f"Before: {(await sandbox.exec('cat /app/config.txt')).stdout.strip()}")

        t0 = time.time()
        await sandbox.checkpoint("safe-config")
        print(f"Checkpoint saved: {(time.time() - t0) * 1000:.0f}ms")

        await sandbox.exec("echo 'BROKEN' > /app/config.txt")
        print(f"After breaking: {(await sandbox.exec('cat /app/config.txt')).stdout.strip()}")

        t0 = time.time()
        await sandbox.revert("safe-config")
        print(f"Reverted: {(time.time() - t0) * 1000:.0f}ms")
        print(f"Restored: {(await sandbox.exec('cat /app/config.txt')).stdout.strip()}")

        # C: Multiple checkpoints
        section("C: Multiple Checkpoints")
        for version in ["v1", "v2", "v3"]:
            await sandbox.exec(f"echo '{version}' > /app/state.txt")
            await sandbox.checkpoint(version)
            print(f"  Saved checkpoint '{version}'")

        print(f"\nCheckpoints: {[c.name for c in sandbox.list_checkpoints()]}")

        for target in ["v1", "v3", "v2"]:
            await sandbox.revert(target)
            r = await sandbox.exec("cat /app/state.txt")
            print(f"  Reverted to '{target}': {r.stdout.strip()}")

        print(f"\nTrajectory: {len(sandbox.get_trajectory())} commands executed")

    asyncio.run(run_daytona())

elif args.backend == "e2b":
    from shepherd_sandboxes.e2b import E2BSandbox

    print("Creating E2B sandbox...")
    with E2BSandbox() as sandbox:
        print(f"Sandbox: {sandbox.sandbox_id}")
        print(f"OverlayFS: {sandbox.using_overlay}")

        # A: Basic exec
        section("A: Basic Execution")
        r = sandbox.exec("echo 'Hello from E2B!'")
        print(f"Output: {r.stdout.strip()}")
        print(f"Latency: {r.duration_ms:.0f}ms")

        # B: Checkpoint and revert
        section("B: Checkpoint & Revert")
        sandbox.exec("sudo sh -c 'echo original config > /app/config.txt'")
        print(f"Before: {sandbox.exec('cat /app/config.txt').stdout.strip()}")

        t0 = time.time()
        sandbox.checkpoint("safe-config")
        print(f"Checkpoint saved: {(time.time() - t0) * 1000:.0f}ms")

        sandbox.exec("sudo sh -c 'echo BROKEN > /app/config.txt'")
        print(f"After breaking: {sandbox.exec('cat /app/config.txt').stdout.strip()}")

        t0 = time.time()
        sandbox.revert("safe-config")
        print(f"Reverted: {(time.time() - t0) * 1000:.0f}ms")
        print(f"Restored: {sandbox.exec('cat /app/config.txt').stdout.strip()}")

        # C: Multiple checkpoints
        section("C: Multiple Checkpoints")
        for version in ["v1", "v2", "v3"]:
            sandbox.exec(f"sudo sh -c 'echo {version} > /app/state.txt'")
            sandbox.checkpoint(version)
            print(f"  Saved checkpoint '{version}'")

        print(f"\nCheckpoints: {[c.name for c in sandbox.list_checkpoints()]}")

        for target in ["v1", "v3", "v2"]:
            sandbox.revert(target)
            r = sandbox.exec("cat /app/state.txt")
            print(f"  Reverted to '{target}': {r.stdout.strip()}")

        print(f"\nTrajectory: {len(sandbox.get_trajectory())} commands executed")

elif args.backend == "k8s":
    from shepherd_sandboxes.kubernetes import K8sSandbox

    print("Creating K8s pod...")
    with K8sSandbox(image=args.image, kubeconfig=args.kubeconfig) as sandbox:
        print(f"Pod: {sandbox.pod_name}")
        print(f"OverlayFS: {sandbox.using_overlay}")

        # A: Basic exec
        section("A: Basic Execution")
        r = sandbox.exec("echo 'Hello from Kubernetes!'")
        print(f"Output: {r.stdout.strip()}")
        print(f"Latency: {r.duration_ms:.0f}ms")

        # B: Checkpoint and revert
        section("B: Checkpoint & Revert")
        sandbox.exec("echo 'original config' > /app/config.txt")
        print(f"Before: {sandbox.exec('cat /app/config.txt').stdout.strip()}")

        t0 = time.time()
        sandbox.checkpoint("safe-config")
        print(f"Checkpoint saved: {(time.time() - t0) * 1000:.0f}ms")

        sandbox.exec("echo 'BROKEN' > /app/config.txt")
        print(f"After breaking: {sandbox.exec('cat /app/config.txt').stdout.strip()}")

        t0 = time.time()
        sandbox.revert("safe-config")
        print(f"Reverted: {(time.time() - t0) * 1000:.0f}ms")
        print(f"Restored: {sandbox.exec('cat /app/config.txt').stdout.strip()}")

        # C: Multiple checkpoints
        section("C: Multiple Checkpoints")
        for version in ["v1", "v2", "v3"]:
            sandbox.exec(f"echo '{version}' > /app/state.txt")
            sandbox.checkpoint(version)
            print(f"  Saved checkpoint '{version}'")

        print(f"\nCheckpoints: {[c.name for c in sandbox.list_checkpoints()]}")

        for target in ["v1", "v3", "v2"]:
            sandbox.revert(target)
            r = sandbox.exec("cat /app/state.txt")
            print(f"  Reverted to '{target}': {r.stdout.strip()}")

        print(f"\nTrajectory: {len(sandbox.get_trajectory())} commands executed")

else:
    print(f"Unknown backend: {args.backend}")
    sys.exit(1)

# =============================================================================
# Summary
# =============================================================================

section("Summary")
print(f"""
Backend: {args.backend}
All operations completed successfully.

Key takeaways:
  1. checkpoint(name) saves container state instantly (~150ms with OverlayFS)
  2. revert(name) restores to any saved checkpoint
  3. Multiple checkpoints supported — jump to any one
  4. Same interface across all backends

Next steps:
  - See docs/remote/overview.md for backend comparison
  - Try running benchmark tasks with checkpoint/revert
  - Use with Agent class: agent.fork() / agent.revert()
""")
