.PHONY: all format lint test tests test_watch test_unit clean install build typecheck

# Default test target
TEST_FILE ?= tests/

# Frozen dependencies for CI reproducibility
.EXPORT_ALL_VARIABLES:
UV_FROZEN = true

# Lint both `src/` and `tests/` when present (some packages may not have tests yet).
RUFF_PATHS = src
ifneq ($(wildcard tests),)
RUFF_PATHS += tests
endif

# Run all tests
test tests:
	@if [ ! -d tests ]; then \
		echo "No tests/ directory; skipping"; \
	else \
		uv run --group test pytest $(TEST_FILE) || { rc=$$?; if [ $$rc -eq 5 ]; then echo "No tests collected; skipping"; exit 0; else exit $$rc; fi; }; \
	fi

# Run tests in watch mode
test_watch:
	@if [ ! -d tests ]; then \
		echo "No tests/ directory; skipping"; \
	else \
		uv run --group test ptw --now . -vv -- $(TEST_FILE); \
	fi

# Run unit tests only (no network)
test_unit:
	@if [ ! -d tests/unit ]; then \
		echo "No tests/unit/ directory; skipping"; \
	else \
		uv run --group test pytest tests/unit/ --disable-socket; \
	fi

# Lint code
lint:
	uv run --group lint ruff check $(RUFF_PATHS)
	uv run --group lint ruff format $(RUFF_PATHS) --diff

# Format code
format:
	uv run --group lint ruff format $(RUFF_PATHS)
	uv run --group lint ruff check --fix $(RUFF_PATHS)

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
# Usage: make test TEST_FILE=tests/test_github.py
