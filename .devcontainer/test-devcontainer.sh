#!/usr/bin/env bash
# test-devcontainer.sh — verify the devcontainer environment
set -uo pipefail

PASS=0
FAIL=0
SKIP=0
FULL=false

if [[ "${1:-}" == "--full" ]]; then
    FULL=true
fi

pass() { echo "  PASS: $1"; ((PASS++)); }
fail() { echo "  FAIL: $1"; ((FAIL++)); }
skip() { echo "  SKIP: $1"; ((SKIP++)); }

run_test() {
    local name="$1"
    shift
    if "$@" > /dev/null 2>&1; then
        pass "$name"
    else
        fail "$name"
    fi
}

echo "=== Devcontainer Test Suite ==="
echo ""

# 1. Python 3.11+
echo "[1/16] Python 3.11+"
run_test "Python 3.11+" python3 -c "import sys; assert sys.version_info >= (3, 11)"

# 2. uv installed
echo "[2/16] uv installed"
run_test "uv installed" uv --version

# 3. Git installed
echo "[3/16] Git installed"
run_test "Git installed" git --version

# 4. GitHub CLI
echo "[4/16] GitHub CLI"
run_test "GitHub CLI" gh --version

# 5. Podman installed
echo "[5/16] Podman installed"
run_test "Podman installed" podman --version

# 6. Podman runs containers (sudo for nested container context)
echo "[6/16] Podman runs containers"
if output=$(sudo podman run --rm docker.io/library/alpine:latest echo hello 2>&1) && [[ "$output" == *"hello"* ]]; then
    pass "Podman runs containers"
else
    fail "Podman runs containers"
fi

# 7. OverlayFS mount/unmount (uses tmpfs base to avoid overlay-on-overlay)
echo "[7/16] OverlayFS mount/unmount"
OVERLAY_TEST_DIR=$(mktemp -d)
sudo mount -t tmpfs tmpfs "$OVERLAY_TEST_DIR"
mkdir -p "$OVERLAY_TEST_DIR"/{lower,upper,work,merged}
echo "test-content" > "$OVERLAY_TEST_DIR/lower/testfile"
if sudo mount -t overlay overlay \
    -o "lowerdir=$OVERLAY_TEST_DIR/lower,upperdir=$OVERLAY_TEST_DIR/upper,workdir=$OVERLAY_TEST_DIR/work" \
    "$OVERLAY_TEST_DIR/merged" 2>/dev/null \
    && [[ "$(cat "$OVERLAY_TEST_DIR/merged/testfile" 2>/dev/null)" == "test-content" ]]; then
    pass "OverlayFS mount/unmount"
else
    fail "OverlayFS mount/unmount"
fi
sudo umount "$OVERLAY_TEST_DIR/merged" 2>/dev/null
sudo umount "$OVERLAY_TEST_DIR" 2>/dev/null
rm -rf "$OVERLAY_TEST_DIR"

# 8. Sandbox image builds (sudo for nested container context)
echo "[8/16] Sandbox image builds"
if sudo podman build -t shepherd-sandbox-test containers/sandbox/ > /dev/null 2>&1; then
    pass "Sandbox image builds"
    sudo podman rmi shepherd-sandbox-test > /dev/null 2>&1 || true
else
    fail "Sandbox image builds"
fi

# 9. Sandbox container runs (sudo for nested container context)
echo "[9/16] Sandbox container runs"
if output=$(sudo podman run --rm --security-opt label=disable shepherd-sandbox python -c "print('ok')" 2>&1) && [[ "$output" == *"ok"* ]]; then
    pass "Sandbox container runs"
else
    fail "Sandbox container runs"
fi

# 10. uv sync works
echo "[10/16] uv sync works"
run_test "uv sync works" uv sync --all-packages --all-groups --all-extras

# 11. Framework imports
echo "[11/16] Framework imports"
run_test "Framework imports" uv run python -c "import shepherd_core; import shepherd_providers; import shepherd_contexts"

# 12-14: Full tests (make lint/typecheck/test)
if $FULL; then
    echo "[12/16] make lint"
    run_test "make lint" make lint

    echo "[13/16] make typecheck"
    run_test "make typecheck" make typecheck

    echo "[14/16] make test"
    run_test "make test" make test
else
    echo "[12/16] make lint"
    skip "make lint (use --full to run)"
    echo "[13/16] make typecheck"
    skip "make typecheck (use --full to run)"
    echo "[14/16] make test"
    skip "make test (use --full to run)"
fi

# 15. Claude Code installed
echo "[15/16] Claude Code installed"
run_test "Claude Code installed" claude --version

# 16. Codex installed
echo "[16/16] Codex installed"
run_test "Codex installed" codex --version

# API key check (informational, not pass/fail)
echo ""
echo "--- API Key Status ---"
for key in ANTHROPIC_API_KEY OPENAI_API_KEY GITHUB_TOKEN; do
    if [ -n "${!key:-}" ]; then
        echo "  $key: set"
    else
        echo "  $key: not set (SKIP)"
    fi
done

# Summary
echo ""
echo "=== Results ==="
echo "  PASS: $PASS  FAIL: $FAIL  SKIP: $SKIP"

if [[ $FAIL -gt 0 ]]; then
    exit 1
else
    exit 0
fi
