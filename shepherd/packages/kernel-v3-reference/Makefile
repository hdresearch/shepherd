.PHONY: all format lint test tests clean install build typecheck

TEST_FILE ?= tests/

all: install

test tests:
	uv run --group dev pytest $(TEST_FILE)

lint:
	uv run --group lint ruff check src/ tests/
	uv run --group lint ruff format src/ tests/ --diff

format:
	uv run --group lint ruff format src/ tests/
	uv run --group lint ruff check --fix src/ tests/

typecheck:
	@echo "No typecheck target is configured for shepherd-kernel-v3-reference yet."

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

install:
	uv sync --all-groups

build:
	uv build
