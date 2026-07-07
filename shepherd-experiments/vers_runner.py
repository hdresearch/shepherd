"""Vers VM execution backend for Shepherd experiments.

Replaces local Seatbelt/Landlock jails with actual Vers VM isolation.
Each sub-agent run:
  1. Branches a VM from the ``shepherd-agent:latest`` golden image
  2. Pushes workspace state (git bundle) + task prompt into the VM
  3. Executes Claude inside the VM (Linux Landlock jail)
  4. Pulls back output files via ``vers exec``
  5. Deletes the VM

The overseer runs locally and reads results through this module.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Golden image reference — committed VM with git, python3, claude CLI, anthropic SDK
GOLDEN_IMAGE_COMMIT = "54e730b4-1a21-4495-b638-c9183d8bde26"
GOLDEN_IMAGE_REF = "shepherd-agent:latest"

# PATH inside the VM
VM_PATH = "/root/.local/bin:/root/.nvm/versions/node/v22.22.3/bin:/usr/local/bin:/usr/bin:/bin"

# Working directory inside the VM
VM_WORKSPACE = "/tmp/shepherd-workspace"


@dataclass
class VersVMRun:
    """Result of a single sub-agent run on a Vers VM."""
    vm_id: str
    status: str  # "ok" | "error"
    changed_files: dict[str, bytes]  # path -> content for all files written
    elapsed_s: float
    error: str | None = None
    claude_output: str = ""


def _vers_exec(vm_id: str, command: str, *, timeout: int = 300, use_ssh: bool = False) -> subprocess.CompletedProcess:
    """Execute a bash command inside a Vers VM.

    Uses ``--ssh`` for commands that may exceed the API's 30-second hard limit.
    The API exec path is faster for short commands (~600ms) but caps at 30s.
    SSH has ~2s overhead but supports arbitrary durations.
    """
    if use_ssh or timeout > 25:
        cmd = ["vers", "exec", "--ssh", "--timeout", str(timeout), vm_id, "bash", "-c", command]
    else:
        cmd = ["vers", "exec", "--timeout", str(timeout), vm_id, "bash", "-c", command]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout + 60,  # local timeout with margin for SSH setup
    )


def _branch_vm(*, count: int = 1) -> list[str]:
    """Branch N VMs from the golden image. Returns list of VM IDs."""
    result = subprocess.run(
        [
            "vers", "branch", GOLDEN_IMAGE_COMMIT,
            "--wait", "--format", "json",
            "--count", str(count),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to branch VM: {result.stderr}")
    data = json.loads(result.stdout)
    return data["new_ids"]


def _delete_vm(vm_id: str) -> None:
    """Delete a VM (best-effort, non-blocking)."""
    subprocess.run(
        ["vers", "delete", "-y", vm_id],
        capture_output=True,
        timeout=30,
    )


def _push_workspace(vm_id: str, workspace_dir: Path) -> None:
    """Push workspace state into the VM via git bundle."""
    with tempfile.NamedTemporaryFile(suffix=".bundle", delete=False) as f:
        bundle_path = f.name

    try:
        # Create git bundle of the workspace
        subprocess.run(
            ["git", "bundle", "create", bundle_path, "--all"],
            cwd=workspace_dir,
            check=True,
            capture_output=True,
        )
        # Read and base64 encode the bundle
        bundle_data = Path(bundle_path).read_bytes()
        b64 = base64.b64encode(bundle_data).decode("ascii")

        # Push into VM: decode bundle, clone it into workspace
        # Split large payloads into chunks to avoid argument length limits
        chunk_size = 50000  # ~50KB chunks
        chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)]

        # Write chunks to a temp file in the VM
        _vers_exec(vm_id, f"rm -rf {VM_WORKSPACE} && mkdir -p {VM_WORKSPACE}", timeout=15)

        for i, chunk in enumerate(chunks):
            op = ">>" if i > 0 else ">"
            _vers_exec(vm_id, f"echo -n '{chunk}' {op} /tmp/bundle.b64", timeout=15)

        _vers_exec(vm_id, f"""
export PATH={VM_PATH}
cd {VM_WORKSPACE}
base64 -d /tmp/bundle.b64 > /tmp/workspace.bundle
git clone /tmp/workspace.bundle . 2>&1
git config user.email "shepherd@vers.sh"
git config user.name "Shepherd Agent"
rm -f /tmp/bundle.b64 /tmp/workspace.bundle
""", timeout=20)

    finally:
        os.unlink(bundle_path)


def _run_claude_task(vm_id: str, prompt: str, *, timeout: int = 300) -> str:
    """Run Claude inside the VM with the given prompt. Returns Claude's text output.

    Writes the prompt to a file inside the VM to avoid shell escaping issues
    with backticks, quotes, and special characters in hypothesis descriptions.
    Uses a Python wrapper script to invoke Claude so we never pass the prompt
    through shell expansion.
    """
    # Base64-encode the prompt to avoid any shell escaping issues
    prompt_b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")

    # Write a small Python script that decodes the prompt and invokes Claude
    # This avoids all shell quoting/expansion issues
    result = _vers_exec(vm_id, f"""
export PATH={VM_PATH}
source /etc/environment
cd {VM_WORKSPACE}

# Decode prompt to file
echo '{prompt_b64}' | base64 -d > /tmp/claude_prompt.txt

# Invoke Claude via Python subprocess to avoid shell expansion of prompt
python3 -c "
import subprocess, sys
with open('/tmp/claude_prompt.txt') as f:
    prompt = f.read()
result = subprocess.run(
    ['claude', '-p', prompt, '--allowedTools', 'Bash,Write,Read', '--output-format', 'text'],
    capture_output=True, text=True, timeout={timeout - 30},
    cwd='{VM_WORKSPACE}',
    env=dict(__import__('os').environ),
)
sys.stdout.write(result.stdout or '')
sys.stderr.write(result.stderr or '')
sys.exit(result.returncode)
" 2>&1

rm -f /tmp/claude_prompt.txt
""", timeout=timeout)

    return (result.stdout or "") + (result.stderr or "")


def _collect_output(vm_id: str) -> dict[str, bytes]:
    """Collect all modified/new files from the workspace in the VM.

    Uses ``find`` to discover all non-git files, then checks ``git status`` to
    identify which are new/modified vs already committed.  Falls back to
    collecting any file that isn't in the original commit.
    """
    # Use git diff + find to discover all new/modified files
    result = _vers_exec(vm_id, f"""
export PATH={VM_PATH}
cd {VM_WORKSPACE} 2>/dev/null || exit 0
# Stage everything and get the diff
git add -A 2>/dev/null
# Get all changed (new + modified) files
git diff --cached --name-only 2>/dev/null
""", timeout=15)

    changed_paths = [
        p.strip() for p in result.stdout.strip().splitlines()
        if p.strip() and "__pycache__" not in p and not p.strip().endswith(".pyc")
    ]
    files: dict[str, bytes] = {}

    for path in changed_paths:
        # Read each file, base64-encode it for safe transport
        result = _vers_exec(vm_id, f"""
cd {VM_WORKSPACE}
if [ -f '{path}' ]; then
    base64 '{path}'
fi
""", timeout=15)
        if result.stdout.strip():
            try:
                files[path] = base64.b64decode(result.stdout.strip())
            except Exception:
                pass

    return files


def run_agent_on_vers(
    workspace_dir: Path,
    prompt: str,
    *,
    timeout: int = 300,
    log_fn: Any = None,
) -> VersVMRun:
    """Execute a single sub-agent task on a fresh Vers VM.

    1. Branch from golden image
    2. Push workspace state
    3. Run Claude with prompt
    4. Collect output files
    5. Delete the VM
    """
    _log = log_fn or (lambda msg: print(msg))

    t0 = time.perf_counter()
    vm_id = None

    try:
        # 1. Branch VM
        _log("  [vers] Branching VM from golden image...")
        t_branch = time.perf_counter()
        vm_ids = _branch_vm(count=1)
        vm_id = vm_ids[0]
        _log(f"  [vers] VM {vm_id[:12]} ready ({time.perf_counter() - t_branch:.1f}s)")

        # 2. Push workspace
        _log("  [vers] Pushing workspace state...")
        t_push = time.perf_counter()
        _push_workspace(vm_id, workspace_dir)
        _log(f"  [vers] Workspace pushed ({time.perf_counter() - t_push:.1f}s)")

        # 3. Run Claude
        _log("  [vers] Running Claude inside VM...")
        t_claude = time.perf_counter()
        claude_output = _run_claude_task(vm_id, prompt, timeout=timeout)
        _log(f"  [vers] Claude finished ({time.perf_counter() - t_claude:.1f}s)")

        # 4. Collect output
        _log("  [vers] Collecting output files...")
        changed_files = _collect_output(vm_id)
        _log(f"  [vers] Collected {len(changed_files)} file(s): {list(changed_files.keys())}")

        elapsed = time.perf_counter() - t0
        return VersVMRun(
            vm_id=vm_id,
            status="ok",
            changed_files=changed_files,
            elapsed_s=round(elapsed, 3),
            claude_output=claude_output[:2000],
        )

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        return VersVMRun(
            vm_id=vm_id or "unknown",
            status="error",
            changed_files={},
            elapsed_s=round(elapsed, 3),
            error=str(exc),
        )

    finally:
        # 5. Cleanup
        if vm_id:
            _log(f"  [vers] Deleting VM {vm_id[:12]}...")
            _delete_vm(vm_id)


def run_agents_on_vers(
    workspace_dir: Path,
    prompts: list[str],
    *,
    timeout: int = 300,
    log_fn: Any = None,
) -> list[VersVMRun]:
    """Run multiple sub-agent tasks on separate Vers VMs (sequentially for now)."""
    results = []
    for i, prompt in enumerate(prompts):
        _log = log_fn or (lambda msg: print(msg))
        _log(f"\n--- Agent {i + 1}/{len(prompts)} ---")
        result = run_agent_on_vers(
            workspace_dir, prompt, timeout=timeout, log_fn=log_fn,
        )
        results.append(result)
    return results
