# Shepherd Sandbox Container
#
# This image provides the Python runtime with all shepherd package dependencies
# pre-installed. The source code is mounted at runtime via PYTHONPATH.
#
# Build:
#   podman build -t shepherd-sandbox containers/sandbox/
#
# The shepherd framework mounts package sources at:
#   /packages/shepherd-core/src
#   /packages/shepherd-providers/src
#   /packages/shepherd-contexts/src
#   /packages/shepherd/src

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    fuse-overlayfs \
    fuse3 \
    && rm -rf /var/lib/apt/lists/*

# Install all dependencies needed by shepherd packages
# - pydantic: shepherd-core base models
# - RestrictedPython: shepherd-core secure task reconstruction
# - claude-agent-sdk: shepherd-providers[claude] SDK for Claude Code
# - openai: shepherd-providers[openai] SDK for OpenAI/Codex
# - opencode-ai: shepherd-providers[opencode] SDK for OpenCode
RUN pip install --no-cache-dir \
    "pydantic>=2.0" \
    "RestrictedPython>=7.0,<8.0" \
    "claude-agent-sdk>=0.1.0" \
    "openai>=1.66" \
    "opencode-ai>=0.1.0a36"

# Install OpenCode CLI (needed by OpenCode provider to run `opencode serve`)
RUN pip install --no-cache-dir "opencode-ai[cli]>=0.1.0a36" 2>/dev/null || true

# Verify key dependencies are available
RUN python -c "from RestrictedPython import compile_restricted_exec; print('RestrictedPython OK')"
RUN python -c "from claude_agent_sdk import query; print('Claude Agent SDK OK')"
RUN python -c "import openai; print('OpenAI SDK OK')"
RUN python -c "import opencode_ai; print('OpenCode SDK OK')"

# Default working directory for task execution
WORKDIR /task

# Default command (overridden by shepherd framework)
CMD ["python"]
