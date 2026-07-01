#!/usr/bin/env bash
# post-start.sh — runs every time the devcontainer starts
set -euo pipefail

# Load project .env if present (provides API keys for checks below)
if [ -f /workspaces/poc-crank-v2/.env ]; then
    set -a
    source /workspaces/poc-crank-v2/.env
    set +a
fi

# Mount tmpfs for overlay directories — the root filesystem is itself an
# overlay, and Linux cannot create overlay mounts where upperdir/workdir live
# on an existing overlay.  A tmpfs avoids this restriction.
# /tmp/shepherd-overlays     — production overlays (ContainerDevice)
# /tmp/shepherd-test-overlays — integration test overlays
for OVERLAY_TMPFS in /tmp/shepherd-overlays /tmp/shepherd-test-overlays; do
    if ! mountpoint -q "$OVERLAY_TMPFS" 2>/dev/null; then
        sudo mkdir -p "$OVERLAY_TMPFS"
        sudo mount -t tmpfs -o size=512M tmpfs "$OVERLAY_TMPFS"
        sudo chown vscode:vscode "$OVERLAY_TMPFS"
        echo "Mounted tmpfs at $OVERLAY_TMPFS"
    fi
done

echo "=== Devcontainer environment check ==="

echo "Python: $(python3 --version)"
echo "uv:     $(uv --version)"

echo -n "OpenCode: "
if command -v opencode &>/dev/null; then
    echo "$(opencode --version 2>/dev/null)"
else
    echo "WARNING - not installed (run: curl -fsSL https://github.com/sst/opencode/releases/latest/download/opencode-linux-$(dpkg --print-architecture).tar.gz | sudo tar xz -C /usr/local/bin/ opencode)"
fi

echo -n "Podman: "
if podman info > /dev/null 2>&1; then
    echo "$(podman --version) (responsive)"
else
    echo "WARNING: podman not responding"
fi

# API key checks (warn only)
for key in ANTHROPIC_API_KEY OPENAI_API_KEY GITHUB_TOKEN; do
    if [ -n "${!key:-}" ]; then
        echo "$key: set"
    else
        echo "$key: WARNING - not set"
    fi
done

# Sandbox image check
if sudo podman image exists shepherd-sandbox 2>/dev/null; then
    echo "Sandbox image: present"
else
    echo "Sandbox image: WARNING - not found (run: sudo podman build -t shepherd-sandbox containers/sandbox/)"
fi

echo "=== Environment check complete ==="
