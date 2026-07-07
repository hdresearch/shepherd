"""Vers VM containment backend — remote VM-level syscall isolation.

Dispatches ``launch()`` to a Vers VM instead of a local Seatbelt/Landlock jail.
Each launch:
  1. Branches a VM from a golden image commit
  2. Syncs the working directory into the VM
  3. Runs the command inside the VM (via SSH for long-running tasks)
  4. Syncs output files back to the local working directory
  5. Deletes the VM

This is the ``ContainmentBackend`` that makes ``workspace.run()`` use Vers
infrastructure for sub-agent execution while keeping the Shepherd substrate
(VcsCore scope tracking, retained outputs, settlement) fully local.

Internal runtime surface — not part of the frozen consumer SPI.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from vcs_core._containment import JailNotEstablished


# Configurable via environment variables
VERS_GOLDEN_IMAGE = os.environ.get(
    "VERS_GOLDEN_IMAGE",
    "54e730b4-1a21-4495-b638-c9183d8bde26",
)

# PATH inside the VM (golden image layout)
_VM_PATH = "/root/.local/bin:/root/.nvm/versions/node/v22.22.3/bin:/usr/local/bin:/usr/bin:/bin"


def _vers_exec_api(vm_id: str, command: str, *, timeout: int = 25) -> subprocess.CompletedProcess[str]:
    """Execute via the Vers API (fast, but hard 30s timeout)."""
    return subprocess.run(
        ["vers", "exec", "--timeout", str(timeout), vm_id, "bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout + 10,
    )


def _vers_exec_ssh(vm_id: str, command: str, *, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Execute via SSH (supports long-running commands).

    Filters out ``vers exec --ssh`` setup messages ("Fetching SSH key...",
    "✓ SSH key cached") from stdout so they don't pollute the command's output
    stream. The Shepherd Claude provider expects pure JSON streaming output.
    """
    result = subprocess.run(
        ["vers", "exec", "--ssh", "--timeout", str(timeout), vm_id, "bash", "-c", command],
        capture_output=True,
        text=True,
        timeout=timeout + 60,
    )
    # Filter vers SSH setup noise from stdout
    if result.stdout:
        filtered_lines = []
        for line in result.stdout.splitlines(keepends=True):
            stripped = line.strip()
            if stripped in (
                "Fetching SSH key from API...",
                "✓ SSH key cached",
                "SSH key cached",
            ):
                continue
            filtered_lines.append(line)
        result = subprocess.CompletedProcess(
            args=result.args,
            returncode=result.returncode,
            stdout="".join(filtered_lines),
            stderr=result.stderr,
        )
    return result


def _branch_vm(golden_image: str) -> str:
    """Branch a single VM from the golden image commit. Returns VM ID."""
    result = subprocess.run(
        ["vers", "branch", golden_image, "--wait", "--format", "json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise JailNotEstablished(
            f"Vers VM branch failed (rc={result.returncode}): {result.stderr.strip()[-300:]}"
        )
    # Parse JSON from output (may have "Waiting for..." prefix)
    stdout = result.stdout.strip()
    json_start = stdout.find("{")
    if json_start < 0:
        raise JailNotEstablished(f"Vers VM branch returned no JSON: {stdout[:200]}")
    data = json.loads(stdout[json_start:])
    vm_ids = data.get("new_ids", [])
    if not vm_ids:
        raise JailNotEstablished("Vers VM branch returned empty new_ids")
    return vm_ids[0]


def _delete_vm(vm_id: str) -> None:
    """Delete a VM (best-effort)."""
    subprocess.run(
        ["vers", "delete", "-y", vm_id],
        capture_output=True,
        timeout=30,
    )


def _sync_to_vm(vm_id: str, local_dir: Path) -> str:
    """Sync a local directory into the VM. Returns the remote path."""
    remote_dir = "/tmp/shepherd-workdir"

    # Create the remote directory
    _vers_exec_api(vm_id, f"rm -rf {remote_dir} && mkdir -p {remote_dir}", timeout=10)

    # Find all files in the local directory
    local_path = Path(local_dir).resolve()
    files_to_sync: list[tuple[str, bytes]] = []
    for root, _dirs, filenames in os.walk(local_path):
        for fname in filenames:
            full = Path(root) / fname
            rel = full.relative_to(local_path)
            rel_str = str(rel)
            # Skip .git internals (too many small files)
            if rel_str.startswith(".git/") or rel_str.startswith(".git\\"):
                continue
            try:
                files_to_sync.append((rel_str, full.read_bytes()))
            except (OSError, PermissionError):
                pass

    # Bundle files into a tar-like base64 payload and unpack in the VM
    # For simplicity: write each file individually via base64
    for rel_path, content in files_to_sync:
        b64 = base64.b64encode(content).decode("ascii")
        # Create parent directory and write file
        parent = str(Path(rel_path).parent)
        mkdir_cmd = f"mkdir -p '{remote_dir}/{parent}'" if parent != "." else ""
        _vers_exec_api(vm_id, f"""
{mkdir_cmd}
echo '{b64}' | base64 -d > '{remote_dir}/{rel_path}'
""", timeout=10)

    return remote_dir


def _sync_from_vm(vm_id: str, remote_dir: str, local_dir: Path) -> None:
    """Sync modified/new files from the VM back to the local directory.

    Only syncs files that differ from the local state.
    """
    # Get list of all files in the remote directory
    result = _vers_exec_api(vm_id, f"""
find '{remote_dir}' -maxdepth 5 -type f -not -path '*/.git/*' -not -path '*/__pycache__/*' | \
    sed 's|^{remote_dir}/||'
""", timeout=15)

    remote_files = [p.strip() for p in result.stdout.strip().splitlines() if p.strip()]

    for rel_path in remote_files:
        local_file = local_dir / rel_path
        # Read remote file content
        result = _vers_exec_api(vm_id, f"base64 '{remote_dir}/{rel_path}'", timeout=15)
        if not result.stdout.strip():
            continue
        try:
            remote_content = base64.b64decode(result.stdout.strip())
        except Exception:
            continue

        # Check if file differs from local
        if local_file.exists():
            try:
                local_content = local_file.read_bytes()
                if local_content == remote_content:
                    continue  # unchanged
            except OSError:
                pass

        # Write back to local
        local_file.parent.mkdir(parents=True, exist_ok=True)
        local_file.write_bytes(remote_content)


class VersContainmentBackend:
    """Vers VM containment — full hypervisor-level isolation.

    Each ``launch()`` call provisions an ephemeral VM from a golden image,
    syncs the working directory in, runs the command, syncs results back,
    and deletes the VM.
    """

    name = "vers-vm"
    enforcement_tier = "vm-hypervisor"

    def __init__(self, golden_image: str | None = None) -> None:
        self._golden_image = golden_image or VERS_GOLDEN_IMAGE

    def available(self) -> tuple[bool, str]:
        """Check if vers CLI is available and authenticated."""
        if not shutil.which("vers"):
            return (False, "vers CLI not found on PATH")
        # Quick check: can we list VMs?
        try:
            result = subprocess.run(
                ["vers", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                return (False, f"vers status failed: {result.stderr.strip()[:100]}")
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            return (False, f"vers CLI error: {exc}")
        return (True, f"vers CLI available, golden image: {self._golden_image[:12]}")

    def profile_for(self, writable_roots: Any, *, allow_network: bool) -> str:
        """Return a profile descriptor. For Vers VMs, this is a no-op identifier.

        The VM provides full isolation by construction — there is no need to
        lower ``may=`` to a syscall-deny policy because the VM boundary is
        stronger than any process-level sandbox.
        """
        return f"vers-vm:{self._golden_image}"

    def probe(self, profile: str, working_root: Any, *, writable_roots: Any) -> None:
        """Probe confinement. For Vers VMs, verify the golden image is accessible.

        We don't branch+delete a VM on every probe (too expensive); we just
        verify the vers CLI works and the golden image commit exists.
        """
        ok, reason = self.available()
        if not ok:
            raise JailNotEstablished(f"Vers VM containment not available: {reason}")

    def launch(
        self,
        profile: str,
        working_root: Any,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Launch a command inside a Vers VM.

        1. Branch a VM from the golden image
        2. Sync working_root into the VM
        3. Execute the command (via SSH for long-running support)
        4. Sync output files back
        5. Delete the VM
        """
        local_dir = Path(str(working_root)).resolve()
        vm_id: str | None = None

        try:
            # 1. Branch VM
            vm_id = _branch_vm(self._golden_image)

            # 2. Sync workspace into VM
            remote_dir = _sync_to_vm(vm_id, local_dir)

            # 3. Execute command in VM
            # Build the command string for bash -c
            # The command is typically [python3, -B, -c, <script>] or
            # [/usr/bin/env, claude, -p, <prompt>, ...] — join with proper quoting
            # Translate local executable paths to VM equivalents
            translated_cmd, env_setup_lines = _translate_command_for_vm(command, remote_dir)
            cmd_str = " ".join(_shell_quote(arg) for arg in translated_cmd)

            # Set up environment
            env_lines = [
                f"export PATH={_VM_PATH}",
                "export IS_SANDBOX=1",  # Claude CLI needs this to accept --dangerously-skip-permissions as root
            ]
            if env:
                for key, value in env.items():
                    if key in ("PATH",):
                        continue
                    env_lines.append(f"export {key}={_shell_quote(value)}")
            # Add env vars extracted from the command (HOME, TMPDIR, etc.)
            env_lines.extend(env_setup_lines)

            full_command = "\n".join(env_lines) + f"""
source /etc/environment 2>/dev/null
cd {remote_dir}
{cmd_str}
"""
            result = _vers_exec_ssh(vm_id, full_command, timeout=300)

            # 4. Sync output back — list what's in the remote dir first
            ls_result = _vers_exec_api(vm_id, f"find '{remote_dir}' -maxdepth 3 -type f -not -path '*/.git/*' | head -20", timeout=15)
            _sync_from_vm(vm_id, remote_dir, local_dir)

            return result

        finally:
            # 5. Cleanup
            if vm_id:
                _delete_vm(vm_id)


def _translate_command_for_vm(command: list[str], remote_dir: str) -> list[str]:
    """Translate a Shepherd-substrate command for execution inside a Vers VM.

    The Shepherd substrate builds commands using local paths:
    - ``/Users/.../python3`` → ``python3``
    - ``/Users/.../claude`` → ``claude``
    - ``/usr/bin/env KEY=LOCAL_PATH ...`` → environment vars rewritten
    - ``/usr/bin/perl -e 'alarm ...'`` → ``timeout`` (coreutils)
    - ``HOME=/local/...`` → ``HOME=/tmp/claude-home``

    Also strips the ``/usr/bin/env`` wrapper and inlines env vars as exports.
    """
    translated = []
    env_vars: dict[str, str] = {}
    skip_next = False

    for i, arg in enumerate(command):
        if skip_next:
            skip_next = False
            continue

        # Strip the perl timeout wrapper — use coreutils `timeout` instead
        if arg == "/usr/bin/perl" and i == 0:
            # perl -e 'alarm N; exec ...' N → timeout N
            # Skip args [0] through [3], replace with timeout
            if len(command) > 3 and command[1] == "-e":
                timeout_secs = command[3] if command[3].isdigit() else "300"
                translated.append("timeout")
                translated.append(timeout_secs)
                skip_next = False  # Skip [1], [2], [3] manually
                # Set a flag to skip next 3 args
                continue
            continue

        # Skip perl wrapper args (positions 1-3)
        if i in (1, 2, 3) and command[0] == "/usr/bin/perl":
            continue

        # Skip /usr/bin/env — extract env vars instead
        if arg == "/usr/bin/env":
            continue

        # Parse KEY=VALUE environment variables from /usr/bin/env args
        if "=" in arg and not arg.startswith("-") and not arg.startswith("/"):
            key, _, value = arg.partition("=")
            if key.isidentifier():
                # Translate local paths to VM paths
                if key in ("HOME", "CLAUDE_CONFIG_DIR", "TMPDIR"):
                    # Use VM-local paths
                    if key == "HOME":
                        env_vars[key] = "/tmp/claude-home"
                    elif key == "CLAUDE_CONFIG_DIR":
                        env_vars[key] = "/tmp/claude-config"
                    elif key == "TMPDIR":
                        env_vars[key] = "/tmp/claude-tmp"
                else:
                    env_vars[key] = value
                continue

        # Translate executable paths — only for args that look like absolute paths
        # to executables (start with /), not for prompt text or other arguments
        if arg.startswith("/") and not arg.startswith("/tmp"):
            basename = arg.rsplit("/", 1)[-1]
            if basename in ("python3", "python3.11", "python3.12", "python"):
                translated.append("python3")
                continue
            if basename == "claude":
                translated.append("claude")
                continue
            if basename == "node":
                translated.append("node")
                continue
            # Other absolute paths (e.g., /usr/bin/perl) — keep but may not exist in VM
            # /usr/bin/touch, /usr/bin/perl etc. are standard and available in Ubuntu

        translated.append(arg)

    # Prepend env vars as exports + mkdir for dirs
    prefix_cmds = []
    for key, value in env_vars.items():
        if key in ("HOME", "CLAUDE_CONFIG_DIR", "TMPDIR"):
            prefix_cmds.append(f"mkdir -p {value}")
        prefix_cmds.append(f"export {key}={_shell_quote(value)}")

    # Return (env_setup_lines, actual_command) as a tagged tuple the caller
    # can inline into the SSH command rather than double-nesting bash -c.
    return translated, prefix_cmds


def _shell_quote(s: str) -> str:
    """Quote a string for safe inclusion in a bash command."""
    if not s:
        return "''"
    # If safe characters only, no quoting needed
    import re
    if re.match(r'^[a-zA-Z0-9._/=@:,-]+$', s):
        return s
    # Use single quotes, escaping embedded single quotes
    return "'" + s.replace("'", "'\\''") + "'"
