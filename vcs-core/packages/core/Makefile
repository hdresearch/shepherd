.PHONY: all format lint test tests test_watch smoke guide_check test_unit test_loopback test_installed test_container coverage clean install build typecheck podman_check podman_build podman_up podman_exec podman_exec_script podman_shell podman_demo podman_session_smoke podman_capture_smoke podman_shell_capture_smoke podman_logs podman_inspect podman_down podman_reset

# Default test target
TEST_FILE ?= tests/
PODMAN_SCRIPT := ../../../scripts/vcs-core-podman.sh
PODMAN_RUN_ENV = $(if $(RUN_NAME),VCS_CORE_PODMAN_RUN_NAME="$(RUN_NAME)") $(if $(KEEP_RUN),VCS_CORE_PODMAN_KEEP_RUN="$(KEEP_RUN)")

# Frozen dependencies for CI reproducibility
.EXPORT_ALL_VARIABLES:
UV_FROZEN = true

# Run all tests. The full suite runs under pytest-xdist (`-n auto`); override
# with `PYTEST_XDIST=` to run serially (e.g. for a single TEST_FILE during dev).
PYTEST_XDIST ?= -n auto
test tests:
	uv run --with pytest-xdist --group test pytest $(PYTEST_XDIST) $(TEST_FILE)

# Run tests in watch mode
test_watch:
	uv run --group test ptw --now . -vv -- $(TEST_FILE)

# Run a narrow package-local CLI happy-path smoke.
smoke:
	uv run --group test pytest tests/integration/cli/test_smoke.py --disable-socket -q

# Validate the endorsed store-first guide against the public package API.
guide_check:
	uv run --group test pytest tests/integration/test_store_first_guide.py tests/unit/test_docs_contract.py --disable-socket -q

# Run the broader non-container package test target with network sockets disabled.
test_unit:
	uv run --group test pytest tests/ -m "not container and not loopback" --disable-socket --allow-unix-socket

# Run loopback-local broker tests that intentionally open TCP sockets.
test_loopback:
	uv run --group test pytest tests/ -m "loopback and not container"

# Run the explicit installed-mode CLI smoke contract.
test_installed:
	uv run --group test pytest tests/integration/cli/test_installed_cli.py -q

# Run container-gated overlay tests.
# On Linux with root: runs natively.
# On macOS (or non-root Linux): builds and runs inside Podman.
CONTAINER_IMAGE ?= vcs-core-test
REPO_ROOT := ../../..
test_container:
	@if [ "$$(uname)" = "Linux" ] && [ "$$(id -u)" = "0" ]; then \
		echo "Running container tests natively (Linux root)..."; \
		uv run --group test pytest tests/ -m container -v && \
		(cd $(REPO_ROOT)/shepherd/packages/dialect && uv sync --group dev --quiet && uv run pytest tests/ -m container -v) && \
		uv run --group test pytest $(REPO_ROOT)/skeleton/tests/test_skeleton_overlay.py -v; \
	else \
		echo "Running container tests via Podman..."; \
		podman build -t $(CONTAINER_IMAGE) -f containers/Containerfile $(REPO_ROOT) && \
		podman run --rm --privileged --security-opt label=disable \
			--tmpfs /tmp:rw,exec,nosuid,nodev \
			-v $$(cd $(REPO_ROOT) && pwd):/workspace \
			-w /workspace \
			$(CONTAINER_IMAGE) \
			sh -c "UV_PROJECT_ENVIRONMENT=/tmp/vcs-core-core-venv uv sync --project vcs-core/packages/core --all-extras --group test --quiet && UV_PROJECT_ENVIRONMENT=/tmp/vcs-core-core-venv uv run --project vcs-core/packages/core pytest vcs-core/packages/core/tests/ -m container -v && UV_PROJECT_ENVIRONMENT=/tmp/shepherd-dialect-venv uv sync --project shepherd/packages/dialect --group dev --quiet && UV_PROJECT_ENVIRONMENT=/tmp/shepherd-dialect-venv uv run --project shepherd/packages/dialect pytest shepherd/packages/dialect/tests/ -m container -v && UV_PROJECT_ENVIRONMENT=/tmp/vcs-core-core-venv uv run --project vcs-core/packages/core pytest skeleton/tests/test_skeleton_overlay.py -v"; \
	fi

# Verify Podman connectivity for the Linux shakeout harness.
podman_check:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) check

# Build the shared Podman shakeout image.
podman_build:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) build

# Start or reuse the shared Podman dev container.
podman_up:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) up

# Run one command inside the shared Podman dev container.
podman_exec:
	@CMD="$${CMD:?set CMD='...'}"; \
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) exec -- bash -c "$$CMD"

# Run one script inside the shared Podman dev container without shell-quoting it as CMD.
podman_exec_script:
	@SCRIPT="$${SCRIPT:?set SCRIPT=...}"; \
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) exec -- bash "$$SCRIPT"

# Open an interactive shell inside the shared Podman dev container.
podman_shell:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) shell

# Run the scripted vcs-core tour inside the shared Podman dev container.
podman_demo:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) demo

# Run the session-shell/session-exec shakeout script inside Podman.
podman_session_smoke:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) session-smoke

# Run the Linux capture shakeout script inside Podman.
podman_capture_smoke:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) capture-smoke

# Run the Linux shell capture shakeout script inside Podman.
podman_shell_capture_smoke:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) shell-capture-smoke

# Show Podman harness logs and retained artifact paths.
podman_logs:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) logs

# Show Podman harness configuration and container state.
podman_inspect:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) inspect

# Stop and remove the shared Podman dev container.
podman_down:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) down

# Remove the shared Podman dev container and local scratch state.
podman_reset:
	$(PODMAN_RUN_ENV) $(PODMAN_SCRIPT) reset

# Run tests with coverage report and threshold enforcement.
# Uses `coverage run` directly to avoid pytest-cov's subprocess
# measurement, which creates incompatible data files when child
# processes (test_run.py) don't inherit branch=true.
coverage:
	rm -f .coverage .coverage.*
	uv run --group test coverage run -m pytest tests/ -m "not container" -q
	uv run --group test coverage report --show-missing

# Lint code
lint:
	uv run --group lint ruff check src/ tests/
	uv run --group lint ruff format src/ tests/ --diff

# Format code
format:
	uv run --group lint ruff format src/ tests/
	uv run --group lint ruff check --fix src/ tests/

# Type checking only
typecheck:
	uv run --group typing mypy src/

# Clean build artifacts
clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Install in development mode
install:
	uv sync --all-groups

# Build package
build:
	uv build

# Run a specific test file
# Usage: make test TEST_FILE=tests/test_store.py
