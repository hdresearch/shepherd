# vcs-core

Provenance-native version control for executable worlds.

A transactional execution infrastructure built on bare Git repositories via pygit2.

## Start Here

The `packages/core` package currently provides the standalone `vcs-core`
runtime, store, CLI, and substrate framework described in `design/`.

For most internal adopters, start with
[`design/guides/GUIDE-store-first.md`](../../design/guides/GUIDE-store-first.md).
That is the default onboarding path for the supported Python owner path:
it uses the Store + VcsCore model directly, stays on the package root
API, and works cross-platform without requiring an overlay runtime,
Linux container, or elevated permissions.

If you specifically need an interactive isolated shell workflow, the
session/overlay path remains the most mature interactive experience
today:

1. `vcs-core init . --adopt git-head --all` for a clean Git checkout, or
   `vcs-core init . --adopt worktree --all` to adopt physical workspace
   files.
2. `vcs-core session start`
3. `vcs-core session shell --scope task --create`
4. work inside the isolated overlay
5. `vcs-core merge task`
6. `vcs-core session stop`
7. `vcs-core push`

Python callers that need this session/overlay capture lane should use the
public `vcs_core.session_capture` facade (`start_capture_session`,
`CaptureSession.exec_capture`, `merge`, `discard`). The daemon socket protocol
and `_ipc` / `_session` / `_cli_session_runtime` modules are private transport.
`start_capture_session` owns the daemon it starts and rejects an already-running
session for the same workspace; `merge` and `discard` return semantic result
objects and raise `SessionCaptureError` on daemon failures.

Plain `vcs-core init .` remains metadata-only. Session overlays are
rendered from vcs-core's store state, not from the physical workspace, so
existing workspace files must be deliberately adopted before session
start. `vcs-core push` is rejected while a persistent session is active;
stop the session before materializing merged store state back to the
physical workspace.

For execution-oriented reads, prefer:

- `vcs-core operations`
- `vcs-core operation show <operation-id>`
- `vcs-core recovery`

While a session daemon is active:

- `vcs-core status` delegates to the live session surface
- `vcs-core session status` is the fuller overlay-oriented status view
- `vcs-core diff` refuses and redirects to the session-aware status
  surface rather than pretending store diff reflects live overlay
  state
- `vcs-core run` and `archive-orphaned-*` reject until the session is
  stopped

Archived execution output is carrier-aware:

- `operations --archived` and `operation show` report whether archived
  history is carried by an archived operation ref or by discarded-world
  history
- `recovery` stays narrower and reports recovery/debug state rather than
  all archived execution history

`vcs-core log` remains the raw commit-history surface. It includes
structural lifecycle records such as `Init`, `ScopeMerge`, and
`DiscardSnapshot` alongside operation-shaped execution commits.

On Linux, vcs-core also supports command-correlated direct capture for
session commands:

```bash
vcs-core session exec --scope task --create --capture -- ./edit-workspace.sh
vcs-core session shell --scope task-shell --create --capture
```

This enables an `LD_PRELOAD`-based filesystem capture path for the
currently implemented event set:

- `write_open`
- `write_observed`
- `write_close`
- `unlink`
- `metadata_change`

In exec capture, filesystem hook events are linked to the `session exec`
command envelope. In shell capture, Bash prompt commands open equivalent
per-command envelopes before the submitted command runs and finish them before
the next prompt. Foreground prompt commands with complete lifecycle evidence
journal raw `CaptureEvent` records and reduce them into linked
`vcs_core.fs_capture_reduction` operations before merge. Commands that produce
no filesystem effects can still archive as complete with zero reduction effects.
Merge compares the final overlay state against the scope store after reduction,
avoiding duplicate reducer effects while still reconciling uncaptured drift,
including later same-path edits and explicitly incomplete capture.

`capture_status=complete` means the daemon drained and reduced the accepted
command-correlated capture stream. It is not a claim that every possible
workspace mutation path was intercepted; static binaries, unsupported syscall
paths, and shim failures remain covered by overlay reconciliation.

Captured `session exec` has scope-local writer exclusivity while its command
envelope is open. A concurrent command cannot start on that same scope if it
would overlap an active captured command, and a captured command cannot start
while another session-exec command is already open on that scope.

Captured `session shell --capture` holds the same writer exclusivity for the
full shell lifetime via a daemon-owned `vcs_core.session_shell` lease. Per-prompt
shell command envelopes nest under that lease; completing one prompt command
does not release the lease. Starting another scope writer, captured exec, or
second captured shell on the same scope is rejected until the shell exits. On
daemon startup, stale shell leases and stale open command envelopes are archived
as abandoned/incomplete instead of being reused.

Interactive `session shell --capture` is Bash-only and currently Linux-only.
Foreground shell builtins, redirections, heredocs, quoted paths, lower-layer
unlinks, and ordinary child-process writes are covered when the preload
lifecycle drains cleanly. Some shell shapes are intentionally not first-class
complete capture yet: pipelines may miss child lifecycle start evidence,
background/async commands can outlive the prompt envelope, and file descriptors
that cross prompt boundaries are ambiguous. Those cases archive as incomplete
or diagnostic rather than falsely complete. Common reasons include
`missing_process_start`, `background_process_still_running`,
`fd_context_crossed_command`, `dirty_fd_left_open`,
`late_event_after_finalization`, `uncorrelated_capture_event`, and
`shim_context_missing`. Reducer output is skipped for incomplete command
captures and residual overlay reconciliation remains the fallback.

Shell capture helper failures are reported on stderr instead of being treated as
successful capture. If the begin helper fails, the command runs without an active
capture envelope. If the outcome helper fails, the shell clears the active
capture environment before the next prompt to avoid attributing later commands to
a stale operation.

The daemon reports structured hook outcome counts so accepted capture
events that do not persist are diagnosable as no-effect, stale scope,
unsupported, malformed, or failed. A persistence failure also marks the
command capture incomplete so reducer output is not treated as authoritative.

This mode is Linux-only and currently requires a working `cc` on PATH
the first time it runs so the shim can be compiled into `.vcscore/`.

## Workspace Boundary

vcs-core manages workspace state under the initialized workspace root.
The `.vcscore/` directory is vcs-core's control plane and is excluded
from managed user workspace state.

Host environment state outside the managed workspace is pass-through,
untracked, and not reversible by vcs-core. Commands may read or write
global package managers, system services, cloud APIs, user-level caches,
or other host resources; those effects are outside the current host-mode
reversibility contract unless the project represents them inside the
workspace.

For state that should travel with vcs-core history, keep it under the
workspace: use a repo-local `.venv`, local logs/traces/output folders,
and tool cache or prefix settings that point inside the project when
practical. Treat system packages, daemons, credentials, and other
machine prerequisites as external setup documented by the project,
devcontainer, Dockerfile, Nix flake, setup guide, or CI image.

Future managed-runtime checkpoints may provide stronger
environment-level reversibility in containers, VMs, or equivalent
disposable roots. That is a separate runtime substrate, not an expansion
of the host-mode workspace filesystem contract.

## Testing

After syncing the workspace dependencies, use these package-local
commands from `packages/core`:

```bash
# Narrow package-local CLI smoke
make smoke

# Endorsed store-first guide validation
make guide_check

# All tests (container-gated tests auto-skip on unsupported platforms)
make test

# Broad non-container package-local test target
make test_unit

# Explicit wheel-installed CLI smoke
make test_installed

# Coverage report with branch coverage and 80% threshold
make coverage

# Overlay integration tests (require Linux + overlayfs/FUSE)
# On macOS: builds and runs inside Podman automatically
# On Linux with root: runs natively
make test_container

# Persistent Podman harness for interactive Linux shakeout
make podman_up
make podman_shell
make podman_exec CMD='cd /workspace && vcs-core --help'
make podman_exec_script SCRIPT=/workspace/vcs-core/scripts/vcs-core-session-shakeout.sh
make podman_demo
make podman_session_smoke
make podman_capture_smoke
make podman_shell_capture_smoke
make podman_shell RUN_NAME=bug-123
make podman_down
```

For linting, this package uses the shared workspace `ruff.toml` at the
repo root. From `packages/core`, the canonical package-local static-check
loop is:

```bash
make lint
make typecheck
```

That wrapper runs `ruff check src/ tests/` and
`ruff format src/ tests/ --diff` against the shared workspace policy.
`make typecheck` runs the package-local strict `mypy src/` pass.

Use `make smoke` for the shortest package-local CLI check. Use
`make guide_check` when you change `GUIDE-store-first.md` or the
public helper surface it teaches. Use
`make test_unit` for the current broader non-container package target;
that selector is wider than unit tests alone. Use `make test_installed`
for prelaunch handoff/release candidates and for packaging,
installed-mode, or public-boundary changes. Use `make test_container`
when touching overlay/session/runtime flows that depend on Linux
container support.

For the authoritative collaborator support matrix and validation
checklist, see [../../CONTRIBUTING.md](../../CONTRIBUTING.md).

The overlay tests exercise the full `fork` / `merge` / `discard` / `push` lifecycle with real kernel overlayfs and fuse-overlayfs backends. They are marked `@pytest.mark.container` and auto-skip on unsupported platforms, so `make test` is always safe to run anywhere.

When these tests run inside Podman locally, the runner mounts `/tmp` as
`tmpfs` so kernel overlayfs has a supported `upperdir`/`workdir`
filesystem. Without that adjustment, nested kernel-overlay mounts can
fail even in a privileged container.

The Podman-backed `make test_container` target also sets
`UV_PROJECT_ENVIRONMENT=/tmp/vcs-core-core-venv` inside the container so
Linux wheels and in-container virtualenv state do not contaminate the
host workspace `.venv`.

Coverage configuration lives in `pyproject.toml` under `[tool.coverage.*]`. Platform-gated modules (`_kernel_overlay.py`, `_fuse_overlay.py`) and the session daemon (`_session.py`) are omitted from coverage expectations since they require a Linux container environment.

CI runs overlay tests automatically via a privileged container job -- see `.github/workflows/ci.yml`.

## Podman Shakeout

Use the `podman_*` targets when you want a persistent Linux environment
for exploratory CLI work rather than a one-shot container test run.
They wrap `scripts/vcs-core-podman.sh`, keep Linux wheels out of the
host `.venv`, configure a disposable git identity inside the container,
and reuse one named dev container across repeated commands.

Recommended loop:

```bash
make podman_check
make podman_up
make podman_shell
make podman_exec CMD='cd /workspace && vcs-core --help'
make podman_exec_script SCRIPT=/workspace/vcs-core/scripts/vcs-core-session-shakeout.sh
make podman_demo
make podman_session_smoke
make podman_capture_smoke
make podman_shell_capture_smoke
make podman_shell RUN_NAME=bug-123
make podman_logs
make podman_down
```

The supported harness path exposes the installed in-container
`vcs-core` entrypoint directly, so the documented `podman_exec`
examples should work without an extra `uv run --project ...` wrapper.
Inline `CMD=...` values are still parsed by `make`; escape shell
variables and exit status checks as `$$VAR` and `$$?`.
Use `podman_exec_script SCRIPT=/workspace/...` when running a longer
script and avoiding nested `CMD=` quoting matters.

Set `KEEP_RUN=1` or `RUN_NAME=<name>` when you want `podman_exec`,
`podman_exec_script`, `podman_shell`, `podman_demo`, or the shakeout
scripts to use a retained host-visible scratch root under
`vcs-core/packages/core/.podman/runs/`. The underlying harness also
honors `VCS_CORE_PODMAN_KEEP_RUN=1` and
`VCS_CORE_PODMAN_RUN_NAME=<name>`, but the stable operator path is via
the package-local `make` variables.

## Installed Mode

Use `make test_installed` for the installed-wheel release gate when you
need confidence in the real installed `vcs-core` executable instead of
the package-local `uv run` development path. That smoke:

- builds a wheel
- installs it into an isolated virtual environment
- runs the installed `vcs-core` binary in subprocesses
- verifies readback through `checkout ground --dest ...` and `status`

It complements the default package-local test loop. It builds with
`uv build --no-build-isolation` against the already-synced local test
environment, so the smoke stays focused on packaging and installed-runtime
behavior rather than build-backend resolution.

## Supported Surfaces

- Supported Python owner path: `vcs_core`
- Supported substrate-author SPI: `vcs_core.spi`
- Supported substrate conformance kit: `vcs_core.spi.testing`
- Supported consumer/runtime call API: `vcs_core.runtime_api`
- Unpromoted runtime-composition seams remain private and may change or
  disappear before launch
- Supported helper for framework-owned built-ins:
  `build_builtin_substrate_context`
- Planned substrates and features described in design docs are not part
  of the endorsed prelaunch adoption path
- Importable internal modules do not become supported extension seams
  merely because they are importable

Extension authors should read
[`GUIDE-integration.md`](../../design/guides/GUIDE-integration.md) next
for the sanctioned integration and extension path.
Substrate authors should read
[`GUIDE-implementing-a-substrate.md`](../../design/guides/GUIDE-implementing-a-substrate.md)
for the `vcs_core.spi` authoring path and conformance-kit expectations.

For framework-owned built-ins, the supported construction seam is:

```python
from vcs_core import build_builtin_substrate_context
```

That helper is public so built-in substrate examples can stay on the
package root. The underlying `BuiltInSubstrateContext` type remains
framework-internal in the current prelaunch cut.

## Pointers

- Short code map: [ARCHITECTURE.md](ARCHITECTURE.md)
- Design index: [../../design/README.md](../../design/README.md)
- Current architecture/model: [../../design/overview/MODEL.md](../../design/overview/MODEL.md)
- Landed public/internal boundary cut: [../../design/history/prelaunch-slices/landed/PLAN-public-internal-boundary-reset.md](../../design/history/prelaunch-slices/landed/PLAN-public-internal-boundary-reset.md)
- Substrate framework status: [../../design/roadmap/substrate-framework/README.md](../../design/roadmap/substrate-framework/README.md)
- Upstream-aware bundle status: [../../design/roadmap/upstream-aware/README.md](../../design/roadmap/upstream-aware/README.md)
