#!/usr/bin/env bash
# post-create.sh — runs once after the devcontainer is created
set -euo pipefail

# Fix git worktree reference to use container mount path
# The .git file points to the host path which doesn't exist inside the container.
# Rewrite it to point to /project-git/worktrees/<name> (where .git is mounted).
WORKSPACE_DIR="$(pwd)"
if [ -f "$WORKSPACE_DIR/.git" ] && [ -d "/project-git" ]; then
  WORKTREE_NAME=$(basename "$WORKSPACE_DIR")
  if [ -d "/project-git/worktrees/$WORKTREE_NAME" ]; then
    echo "=== Fixing git worktree reference ==="
    echo "gitdir: /project-git/worktrees/$WORKTREE_NAME" > "$WORKSPACE_DIR/.git"
    git config --global --add safe.directory "$WORKSPACE_DIR"
  fi
fi

echo "=== Installing Python dependencies ==="
uv sync --all-packages --all-groups --all-extras

echo "=== Building sandbox image ==="
# Use sudo for podman build — rootless podman can't mount /proc or write
# cgroups inside a Docker-hosted devcontainer.
sudo podman build -t shepherd-sandbox containers/sandbox/

# Copy image to rootless store so tests (which run as vscode) can find it.
echo "=== Copying sandbox image to rootless store ==="
sudo podman save shepherd-sandbox:latest | podman load

echo "=== Installing Claude Code ==="
sudo npm install -g @anthropic-ai/claude-code

echo "=== Installing Codex CLI ==="
sudo npm install -g @openai/codex

echo "=== Adding shell aliases and .env loader ==="
cat >> ~/.bashrc << 'BASHRC'

# Load project .env if present (API keys, config)
if [ -f "$PWD/.env" ]; then
    set -a
    source "$PWD/.env"
    set +a
fi

# Shepherd framework aliases
alias at='uv run pytest'
alias al='uv run ruff check .'
alias af='uv run ruff format .'
alias am='uv run mypy packages/shepherd-core/src/shepherd_core'
BASHRC

echo "=== post-create complete ==="
