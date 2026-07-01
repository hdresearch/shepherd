.PHONY: all format lint test tests clean install build typecheck

# Default test target
TEST_FILE ?= tests/

# Frozen dependencies for CI reproducibility
.EXPORT_ALL_VARIABLES:
UV_FROZEN = true

# Run all tests
test tests:
	@uv run pytest $(TEST_FILE) || { rc=$$?; if [ $$rc -eq 5 ]; then echo "No tests collected; skipping"; exit 0; else exit $$rc; fi; }

# Lint code
lint:
	uv run ruff check src/ tests/
	uv run ruff format src/ tests/ --diff

# Format code
format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

# Type checking only
typecheck:
	uv run mypy src/

# Clean build artifacts
clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# Install in development mode
install:
	uv sync --all-extras

# Build package
build:
	uv build
