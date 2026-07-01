FROM mcr.microsoft.com/devcontainers/python:1-3.11-bookworm

# Remove stale Yarn repo from base image (expired GPG key breaks apt-get update)
RUN rm -f /etc/apt/sources.list.d/yarn.list

# System packages: Podman + networking, dev utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    podman slirp4netns uidmap \
    jq ripgrep build-essential \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Node.js 22 (system nodejs from Debian Bookworm is v18, too old for Claude Code)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Podman configuration for nested container context
RUN mkdir -p /etc/containers

# Use vfs storage driver (works reliably in nested container contexts without
# FUSE or kernel overlay-on-overlay). Slower than overlay but avoids the
# fuse-overlayfs device requirement and overlay nesting issues.
RUN printf '[storage]\ndriver = "vfs"\nrunroot = "/run/containers/storage"\ngraphroot = "/var/lib/containers/storage"\n' \
    > /etc/containers/storage.conf \
    && mkdir -p /run/containers/storage /var/lib/containers/storage

# cgroupfs manager + file events logger (no systemd in containers)
# network_backend=slirp4netns avoids pasta/crun ping_group_range issues
RUN printf '[engine]\ncgroup_manager = "cgroupfs"\nevents_logger = "file"\n\n[network]\ndefault_rootless_network_cmd = "slirp4netns"\n' \
    > /etc/containers/containers.conf

# Docker Hub as default registry
RUN printf 'unqualified-search-registries = ["docker.io"]\n' \
    > /etc/containers/registries.conf

# uv package manager
COPY --from=ghcr.io/astral-sh/uv:0.6 /uv /uvx /usr/local/bin/

# GitHub CLI
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
    | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
    > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y gh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# OpenCode CLI (sst/opencode — needed by OpenCodeProvider for `opencode serve`)
RUN ARCH=$(dpkg --print-architecture) \
    && curl -fsSL "https://github.com/sst/opencode/releases/latest/download/opencode-linux-${ARCH}.tar.gz" \
    | tar xz -C /usr/local/bin/ opencode \
    && chmod +x /usr/local/bin/opencode

# Pre-create overlay directories for the framework
RUN mkdir -p /tmp/shepherd-overlays /home/vscode/.shepherd/overlays \
    && chown -R vscode:vscode /tmp/shepherd-overlays /home/vscode/.shepherd

# Pre-create ~/.claude so that bind-mounting .claude/projects (shared
# transcripts) doesn't leave the parent directory owned by root, which
# blocks Claude Code from writing auth credentials.
RUN mkdir -p /home/vscode/.claude \
    && chown vscode:vscode /home/vscode/.claude

# Pre-create OpenCode data directory so the volume mount in
# devcontainer.json lands on a vscode-owned path (auth.json, opencode.db).
RUN mkdir -p /home/vscode/.local/share/opencode \
    && chown -R vscode:vscode /home/vscode/.local/share/opencode
