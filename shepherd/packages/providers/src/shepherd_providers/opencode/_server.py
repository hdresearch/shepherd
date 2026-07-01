"""OpenCode server lifecycle management.

Manages `opencode serve` processes via a process-global singleton registry.
Servers are keyed by (working directory, model) and reused across execute_sdk()
calls.

Model selection: the OpenCode server reads its model configuration from
``opencode.json`` in its working directory at startup.  The registry writes this
file before starting each server, enabling different model configurations to
coexist (each on a different port).  Because the server reads the config once at
startup, sequential writes to the same file are safe — each server retains the
config it was started with.

The server is started with --port 0 (OS auto-assignment) and the assigned
URL is discovered by parsing stdout for the "listening on" line.
"""

from __future__ import annotations

import asyncio
import atexit
import inspect
import json
import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class ServerStartError(Exception):
    """Failed to start the OpenCode server."""


@dataclass
class OpenCodeServer:
    """Manages a single `opencode serve` process.

    Starts the server, discovers its port via stdout parsing,
    and provides health check and stop operations.
    """

    cwd: str
    port: int | None = None  # None = OS auto-assign via --port 0
    _proc: subprocess.Popen | None = field(default=None, repr=False)  # type: ignore[type-arg]
    _base_url: str | None = field(default=None, repr=False)

    async def start(
        self,
        timeout: float = 15.0,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        """Start the server and discover its assigned port.

        Args:
            timeout: Maximum time to wait for the server to start.
            extra_env: Additional environment variables for the server process.
                Used to pass ``OPENCODE_PERMISSION`` for headless operation.

        Raises:
            ServerStartError: If the server fails to start or times out.
        """
        cmd = ["opencode", "serve"]
        if self.port is not None:
            cmd.extend(["--port", str(self.port)])

        env = {**os.environ, **(extra_env or {})}

        try:
            self._proc = subprocess.Popen(  # noqa: ASYNC220
                cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as e:
            raise ServerStartError("opencode CLI not found. Install with: pip install opencode-ai[cli]") from e

        self._base_url = await self._read_listening_url(timeout)

        # Close stdout and stderr pipes after URL is parsed to prevent buffer
        # accumulation. The server may continue writing; closing the pipes
        # avoids blocking if the buffers fill up.
        import contextlib

        for pipe in (self._proc.stdout, self._proc.stderr):
            if pipe:
                with contextlib.suppress(OSError):
                    pipe.close()

        logger.info(f"OpenCode server started: url={self._base_url} cwd={self.cwd}")

    async def _read_listening_url(self, timeout: float) -> str:
        """Read stdout lines until we find the listening URL."""
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        remainder = b""

        while loop.time() < deadline:
            # Check if process died
            if self._proc is not None and self._proc.poll() is not None:
                stderr = ""
                if self._proc.stderr:
                    stderr = await loop.run_in_executor(
                        None,
                        lambda: self._proc.stderr.read().decode("utf-8", errors="replace"),  # type: ignore[union-attr]
                    )
                raise ServerStartError(f"opencode serve exited with code {self._proc.returncode}: {stderr}")

            # Non-blocking read from stdout
            if self._proc is not None and self._proc.stdout:
                data = await loop.run_in_executor(
                    None,
                    lambda: self._proc.stdout.read1(4096),  # type: ignore[union-attr]
                )
                if data:
                    remainder += data
                    # Split into complete lines + remaining incomplete line
                    parts = remainder.split(b"\n")
                    remainder = parts[-1]  # Keep incomplete last chunk
                    for raw_line in parts[:-1]:
                        line = raw_line.decode("utf-8", errors="replace")
                        if "listening on" in line:
                            return line.split("listening on")[-1].strip()

            await asyncio.sleep(0.1)

        raise ServerStartError(f"Timed out waiting for server to start ({timeout}s)")

    @property
    def base_url(self) -> str:
        """URL of the running server."""
        if self._base_url is None:
            raise RuntimeError("Server not started")
        return self._base_url

    async def health_check(self, timeout: float = 2.0) -> bool:
        """Check if the server is still responding.

        Returns True if the server responds to HTTP on any path.
        OpenCode's server is an SPA that returns 200 text/html on every path.
        """
        if self._proc is None or self._proc.poll() is not None:
            return False

        try:
            import httpx

            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}/", timeout=timeout)
                return resp.status_code == 200
        except (OSError, httpx.HTTPError, RuntimeError):
            return False

    async def stop(self) -> None:
        """Stop the server process (SIGTERM, then SIGKILL after 5s)."""
        if self._proc is None:
            return

        if self._proc.poll() is not None:
            self._proc = None
            return

        logger.info(f"Stopping OpenCode server: url={self._base_url}")
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("OpenCode server did not stop within 5s, sending SIGKILL")
            self._proc.kill()
            self._proc.wait(timeout=5)
        self._proc = None

    def stop_sync(self) -> None:
        """Synchronous stop for atexit handler."""
        if self._proc is None:
            return
        if self._proc.poll() is not None:
            self._proc = None
            return
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=5)
        self._proc = None


# Permissions that default to "ask" in OpenCode but must be "allow" for headless
# (programmatic) execution.  Passed via the ``OPENCODE_PERMISSION`` env var so
# that the server picks them up at startup — no file writes needed.
_HEADLESS_PERMISSIONS: dict[str, str] = {
    "external_directory": "allow",  # access paths outside project root
    "doom_loop": "allow",  # continue after repeated failures
}


def _write_model_config(cwd: str, model: str) -> None:
    """Write the model to ``opencode.json`` in *cwd*, preserving other settings.

    The OpenCode server reads ``opencode.json`` at startup to determine which
    model each agent uses.  This function sets ``agent.build.model`` and ensures
    the provider is registered (e.g. ``"groq": {}``), while leaving everything
    else untouched.

    Permissions are handled separately via the ``OPENCODE_PERMISSION`` env var
    (see ``_HEADLESS_PERMISSIONS``), not written to the config file.

    If the file already specifies the requested model, this is a no-op.
    """
    config_path = Path(cwd) / "opencode.json"

    existing: dict[str, Any] = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Could not read {config_path}, overwriting: {e}")

    # Track whether anything changed so we can skip the write if not needed.
    changed = False

    # Set the model for the "build" agent (the default agent).
    agent = existing.setdefault("agent", {})
    build = agent.setdefault("build", {})
    if build.get("model") != model:
        build["model"] = model
        changed = True

    # Ensure the provider ID is registered so the server recognises it.
    # e.g. model="groq/llama-3.3-70b-versatile" → provider "groq".
    provider_id = model.split("/", 1)[0]
    if provider_id:
        providers = existing.setdefault("provider", {})
        if provider_id not in providers:
            providers[provider_id] = {}
            changed = True

    if not changed:
        return

    config_path.write_text(json.dumps(existing, indent=2) + "\n")
    logger.info(f"Wrote model config: {config_path} → {model}")


class OpenCodeServerRegistry:
    """Process-global singleton that manages OpenCode server instances.

    Servers are keyed by ``(cwd, model)`` — each unique combination gets its
    own server process.  Before starting a server the registry writes the
    requested model to ``{cwd}/opencode.json`` so that the server picks it up
    at startup.  Because the server reads the config only once, sequential
    writes to the same file are safe: each server retains the config it was
    started with.
    """

    _instance: ClassVar[OpenCodeServerRegistry | None] = None
    _instance_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        # Keyed by (cwd, model) — model may be None for "use whatever is configured".
        self._servers: dict[tuple[str, str | None], OpenCodeServer] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_lock = threading.Lock()
        atexit.register(self._cleanup_all)

    @classmethod
    def get_instance(cls) -> OpenCodeServerRegistry:
        """Get or create the singleton registry."""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _get_lock(self, cwd: str) -> asyncio.Lock:
        """Get or create a per-cwd lock.

        Locking is per-cwd (not per-key) because the config file is shared
        across all models in the same cwd — writes must be serialised.
        """
        with self._global_lock:
            if cwd not in self._locks:
                self._locks[cwd] = asyncio.Lock()
            return self._locks[cwd]

    async def get_or_start(
        self,
        cwd: str,
        port: int | None = None,
        model: str | None = None,
    ) -> str:
        """Get a running server for the given cwd and model, starting one if needed.

        Args:
            cwd: Working directory for the server.
            port: Fixed port override, or None for OS auto-assignment.
            model: Model identifier in ``"provider/model"`` format
                (e.g. ``"groq/llama-3.3-70b-versatile"``).  If provided, the
                registry writes the model to ``{cwd}/opencode.json`` before
                starting the server.  If *None*, the server uses whatever model
                is already configured.

        Returns:
            base_url of the healthy server.
        """
        key = (cwd, model)
        lock = self._get_lock(cwd)
        async with lock:
            # Check for existing healthy server
            if key in self._servers:
                server = self._servers[key]
                if await server.health_check():
                    return server.base_url
                # Server is dead — clean up and restart
                logger.warning(f"OpenCode server for {cwd} (model={model}) is unhealthy, restarting")
                await server.stop()
                del self._servers[key]

            # Write model config before starting the server.
            if model is not None:
                _write_model_config(cwd, model)

            # Start a new server with headless permissions via env var.
            server = OpenCodeServer(cwd=cwd, port=port)
            await server.start(
                extra_env={
                    "OPENCODE_PERMISSION": json.dumps(_HEADLESS_PERMISSIONS),
                }
            )
            self._servers[key] = server
            return server.base_url

    async def stop_all(self) -> None:
        """Stop all managed servers."""
        for _key, server in list(self._servers.items()):
            await server.stop()
        self._servers.clear()

    def _cleanup_all(self) -> None:
        """Synchronous cleanup for atexit. Stops all managed servers."""
        for server in self._servers.values():
            result = server.stop_sync()
            if inspect.iscoroutine(result):
                result.close()
        self._servers.clear()


__all__ = ["OpenCodeServer", "OpenCodeServerRegistry", "ServerStartError"]
