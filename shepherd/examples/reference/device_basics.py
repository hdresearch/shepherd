"""Example 07a: Device Basics.

A minimal example showing how the Device abstraction works.
This is the foundation for container execution in Shepherd.

Key concepts:
1. Device selection via context manager
2. LocalDevice (in-process) vs ContainerDevice (isolated)
3. Effects flow back from devices to the host scope

Prerequisites:
- ANTHROPIC_API_KEY in environment or .env file
- For container mode: Podman installed and running (`podman machine start` on macOS)

Run with:
    # Local execution (default, from the repository root)
    uv run python shepherd/examples/reference/device_basics.py

    # Container execution (requires Podman)
    uv run python shepherd/examples/reference/device_basics.py --container
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Add repository root to path for imports
_repo_root = Path(__file__).parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Load environment variables from .env file
from dotenv import load_dotenv

load_dotenv(_repo_root / ".env")  # Load from repo root
load_dotenv()  # Also check current directory


# =============================================================================
# Pre-flight checks
# =============================================================================


def check_podman_available() -> bool:
    """Check if Podman is installed and running."""
    try:
        result = subprocess.run(
            ["podman", "version"],
            check=False,
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_api_key() -> None:
    """Verify ANTHROPIC_API_KEY is set. Exit if not."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("       Set it in your environment or create a .env file:")
        print("       echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env")
        sys.exit(1)


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Example 07a: Device Basics")
    parser.add_argument(
        "--container",
        action="store_true",
        help="Use container execution (requires Podman)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Use local execution (default)",
    )
    return parser.parse_args()


# Parse args early
args = parse_args()
USE_CONTAINER = args.container and not args.local

# Check prerequisites
check_api_key()
if USE_CONTAINER and not check_podman_available():
    print("ERROR: --container specified but Podman is not available.")
    print("       Try: podman machine start")
    print("       Or run without --container for local execution.")
    sys.exit(1)


# =============================================================================
# Imports (after pre-flight checks)
# =============================================================================

import shepherd
from shepherd import ClaudeProvider, Input, Output, scope, task
from shepherd_runtime.device import Device, get_current_device, get_device, list_devices
from pydantic import BaseModel

# =============================================================================
# Tasks
# =============================================================================


@task
class TellJoke(BaseModel):
    """Tell a joke about a topic."""

    topic: Input(str)
    joke: Output(str)


# =============================================================================
# Configuration
# =============================================================================

shepherd.configure(
    provider=ClaudeProvider(
        name="joker",
        model="claude-sonnet-4-20250514",
    )
)

# =============================================================================
# Main Example
# =============================================================================

device_name = "container" if USE_CONTAINER else "local"

print("=" * 60)
print("Example 07a: Device Basics")
print("=" * 60)
print(f"\nExecution mode: {device_name.upper()}")

# -----------------------------------------------------------------------------
# 1. Available devices
# -----------------------------------------------------------------------------
print("\n--- Available Devices ---")
for name in list_devices():
    device = get_device(name)
    caps = device.capabilities
    print(f"  {name}:")
    print(f"    isolation_level: {caps.isolation_level}")
    print(f"    effect_capture: {caps.effect_capture}")

# -----------------------------------------------------------------------------
# 2. Device context manager
# -----------------------------------------------------------------------------
print("\n--- Device Context Manager ---")

# By default, no device is selected (uses in-process execution)
print(f"Current device (default): {get_current_device()}")

# Select a device via context manager
with Device(device_name):
    print(f"Inside Device('{device_name}'): {get_current_device().name}")

    # Tasks execute with this device
    result = TellJoke(topic="Python programming")
    print(f"Joke: {result.joke[:80]}...")

# Device resets after context exits
print(f"After context: {get_current_device()}")

# -----------------------------------------------------------------------------
# 3. Nested device contexts
# -----------------------------------------------------------------------------
print("\n--- Nested Device Contexts ---")

with Device("local"):
    print(f"Outer: {get_current_device().name}")

    # Can nest different devices
    inner_device = "container" if USE_CONTAINER else "local"
    with Device(inner_device):
        print(f"  Inner: {get_current_device().name}")

    print(f"Back to outer: {get_current_device().name}")

# -----------------------------------------------------------------------------
# 4. Scope and device interaction
# -----------------------------------------------------------------------------
print("\n--- Scope and Device Interaction ---")

# Scope can access current device
print(f"scope.current_device: {scope.current_device}")

with Device(device_name):
    print(f"Inside Device: scope.current_device = {scope.current_device.name}")

    # Can also set device explicitly on scope
    local_device = get_device("local")
    scope.set_device(local_device)
    print(f"After set_device: scope.current_device = {scope.current_device.name}")

# -----------------------------------------------------------------------------
# 5. How it works
# -----------------------------------------------------------------------------
print("\n--- How It Works ---")
print("""
    Device Execution Flow:
    ┌─────────────────────────────────────────────────────┐
    │ with Device("container"):                           │
    │     result = MyTask(...)                            │
    └────────────────────────┬────────────────────────────┘
                             │
                             ▼
    ┌─────────────────────────────────────────────────────┐
    │ ExecutionLifecycle checks scope.current_device      │
    │                                                     │
    │ if device.isolation_level != "none":                │
    │     → device.create_sandbox()                       │
    │     → device.execute()        # In container        │
    │     → device.extract_effects()                      │
    │     → Apply effects to scope                        │
    │     → device.cleanup()                              │
    │ else:                                               │
    │     → Execute in-process (existing path)            │
    └─────────────────────────────────────────────────────┘
    """)

# -----------------------------------------------------------------------------
# Summary
# -----------------------------------------------------------------------------
print("--- Summary ---")
print(f"Execution mode: {device_name}")
print(f"Effects captured: {len(scope.effects)}")
print("\nKey points:")
print("  - Device('local'): In-process execution (no isolation)")
print("  - Device('container'): Podman container with OverlayFS")
print("  - Effects flow back to host scope automatically")
print("  - Same task code works on any device")

if not USE_CONTAINER:
    print("\nTip: Run with --container to test container execution")
