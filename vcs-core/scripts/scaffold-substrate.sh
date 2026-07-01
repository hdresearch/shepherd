#!/usr/bin/env bash
# scaffold-substrate.sh — generate the minimal files for a new SPI v0.1
# command-only state substrate.
#
# Usage:
#   scripts/scaffold-substrate.sh <substrate-name>
#   scripts/scaffold-substrate.sh --out-of-tree <substrate-name> [target-dir]
#
# Where <substrate-name> uses dashes (e.g., "memory-state").
#
# In-tree mode (default) emits a skeleton driver + adapter + test files under
# vcs-core core, plus a tracking entry in the SPI bundle's implementer-
# affordances list. The driver imports from the stable `vcs_core.spi` surface,
# inherits BaseSubstrateDriver, and ships with two commands (`checkpoint`,
# `create-candidate`); the adapter subclasses the generic RoleSubstrateAdapter.
#
# Out-of-tree mode (--out-of-tree) emits a standalone package: a pyproject with
# the `vcscore.substrate_plugins` entry point, a `vcs_core.spi`-only driver with
# structural self-checks, a SubstratePlugin, and conformance-kit tests. This is
# Path C in GUIDE-implementing-a-substrate.md.

set -euo pipefail

OUT_OF_TREE=0
if [ "${1:-}" = "--out-of-tree" ]; then
    OUT_OF_TREE=1
    shift
fi

if [ "$OUT_OF_TREE" -eq 0 ] && [ "$#" -ne 1 ]; then
    echo "usage: $0 <substrate-name>" >&2
    echo "       $0 --out-of-tree <substrate-name> [target-dir]" >&2
    echo "example: $0 memory-state" >&2
    exit 1
fi
if [ "$OUT_OF_TREE" -eq 1 ] && { [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; }; then
    echo "usage: $0 --out-of-tree <substrate-name> [target-dir]" >&2
    exit 1
fi

NAME_DASH="$1"
NAME_SNAKE="${NAME_DASH//-/_}"
# Use tr for uppercase rather than ${VAR^^} (bash 4+) so the script
# runs on macOS's default bash 3.2.
NAME_SNAKE_UPPER="$(printf '%s' "$NAME_SNAKE" | tr '[:lower:]' '[:upper:]')"
NAME_PASCAL="$(echo "$NAME_DASH" | awk -F'-' '{for (i=1;i<=NF;i++) printf "%s%s", toupper(substr($i,1,1)), substr($i,2); print ""}')"
NAME_ROLE="shepherd.${NAME_PASCAL}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ===========================================================================
# Out-of-tree mode — a standalone package (Path C)
# ===========================================================================
if [ "$OUT_OF_TREE" -eq 1 ]; then
    TARGET="${2:-./${NAME_DASH}-substrate}"
    PKG="${NAME_SNAKE}_substrate"
    if [ -e "$TARGET" ]; then
        echo "error: $TARGET already exists" >&2
        exit 2
    fi
    mkdir -p "$TARGET/src/$PKG" "$TARGET/tests"

    cat > "$TARGET/pyproject.toml" <<EOF
[project]
name = "${NAME_DASH}-substrate"
version = "0.1.0a1"
description = "Out-of-tree ${NAME_DASH} substrate for vcs-core (SPI v0.1)."
requires-python = ">=3.11"
dependencies = ["vcs-core"]

# vcs-core is alpha and not yet published to a registry, so point this at your
# local checkout for the kit-based tests to resolve. Uncomment and set the path
# (or use a git source). With uv's workspace, this can be \`{ workspace = true }\`.
# [tool.uv.sources]
# vcs-core = { path = "/path/to/vcs-core/packages/core", editable = true }

# vcs-core discovers the driver through this entry-point group; a repo binds it
# by name (\`vcs-core binding add ${NAME_SNAKE} acme.${NAME_SNAKE}\`).
[project.entry-points."vcscore.substrate_plugins"]
"acme.${NAME_SNAKE}" = "${PKG}.plugin:PLUGIN"

[dependency-groups]
dev = ["pytest>=8.0"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/${PKG}"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
EOF

    cat > "$TARGET/src/$PKG/__init__.py" <<EOF
"""Out-of-tree ${NAME_DASH} substrate (SPI v0.1, Path C)."""

from ${PKG}.driver import ${NAME_PASCAL}SubstrateDriver

__all__ = ["${NAME_PASCAL}SubstrateDriver"]
EOF

    cat > "$TARGET/src/$PKG/driver.py" <<EOF
"""${NAME_PASCAL} substrate driver — out-of-tree, SPI v0.1 (Path C).

Imports the SPI vocabulary from the stable \`vcs_core.spi\` home only — never
\`vcs_core._*\` (the no-private-coupling invariant). The module ends with a
structural self-check so a contract regression fails at import time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from vcs_core.spi import (
    BaseSubstrateDriver,
    CapabilitySet,
    CommandRequest,
    DriverContext,
    DriverIngressResult,
    IngressRequest,
    SubstrateDriver,
    TransitionDraft,
    command,
)

${NAME_SNAKE_UPPER}_REVISION_SCHEMA = "acme/${NAME_DASH}-revision/v1"


@dataclass(frozen=True)
class ${NAME_PASCAL}SubstrateDriver(BaseSubstrateDriver):
    """JSON-backed ${NAME_DASH} substrate driver (out-of-tree scaffold)."""

    store_id: str = "store_${NAME_SNAKE}"
    binding: str = "${NAME_SNAKE}"
    role: str = "${NAME_ROLE}"
    driver_id: str = "acme.${NAME_SNAKE}"
    driver_version: str = "v1"
    materialization_class: str = "external"

    @property
    def capabilities(self) -> CapabilitySet:
        # Per SPI v0.1 §Result Shape "Capabilities are a runtime contract",
        # only include request types your prepare handler actually supports.
        return CapabilitySet(accepts=frozenset({CommandRequest}), selectable=False)

    def prepare(self, context: DriverContext, request: IngressRequest) -> DriverIngressResult:
        return self.dispatch_decorated_command(context, request)

    @command("checkpoint")
    def checkpoint(
        self,
        context: DriverContext,
        *,
        payload: Annotated[object, {"description": "canonical ${NAME_DASH} payload"}],
    ) -> DriverIngressResult:
        """Snapshot current ${NAME_DASH} state."""
        return self._prepare_state(context, "checkpoint", payload)

    @command("create-candidate")
    def create_candidate(
        self,
        context: DriverContext,
        *,
        payload: Annotated[object, {"description": "candidate ${NAME_DASH} payload"}],
    ) -> DriverIngressResult:
        """Create a ${NAME_DASH} candidate revision."""
        return self._prepare_state(context, "${NAME_DASH}-json-revision", payload)

    def _prepare_state(self, context: DriverContext, semantic_op: str, payload: object) -> DriverIngressResult:
        del context
        payload_dict: dict[str, Any] = {"schema": ${NAME_SNAKE_UPPER}_REVISION_SCHEMA}
        if isinstance(payload, dict):
            payload_dict.update(payload)
        return DriverIngressResult(
            transitions=(
                TransitionDraft(
                    transition_id="primary",
                    semantic_op=semantic_op,
                    payload=payload_dict,
                    observation_ids=(),
                ),
            ),
        )


# Structural self-check: fail at import time if the driver drifts from the
# SubstrateDriver Protocol (the idiom the dialect / skeleton reference drivers use).
assert isinstance(${NAME_PASCAL}SubstrateDriver(), SubstrateDriver)
EOF

    cat > "$TARGET/src/$PKG/plugin.py" <<EOF
"""Entry-point registration for the out-of-tree ${NAME_DASH} substrate."""

from __future__ import annotations

from vcs_core.manifest import SubstrateManifest, SubstratePlugin

PLUGIN = SubstratePlugin(
    name="acme.${NAME_SNAKE}",
    substrate=("${PKG}.driver", "${NAME_PASCAL}SubstrateDriver"),
    manifest=SubstrateManifest(
        name="acme.${NAME_SNAKE}",
        description="Out-of-tree ${NAME_DASH} substrate",
        tier="explicit",
    ),
    implementation_kind="driver",
)
EOF

    cat > "$TARGET/tests/test_driver.py" <<EOF
"""Conformance + behavioral tests for the out-of-tree ${NAME_DASH} driver.

The exportable kit (\`vcs_core.spi.testing\`) gives built-in-equivalent coverage
without touching vcs-core core's test suite.
"""

from __future__ import annotations

import pytest
from vcs_core.spi import CommandRequest
from vcs_core.spi.testing import assert_substrate_driver_conformant, conformance_cases

from ${PKG}.driver import ${NAME_PASCAL}SubstrateDriver, ${NAME_SNAKE_UPPER}_REVISION_SCHEMA


def test_driver_is_spi_conformant() -> None:
    assert_substrate_driver_conformant(${NAME_PASCAL}SubstrateDriver())


@pytest.mark.parametrize("case", conformance_cases(${NAME_PASCAL}SubstrateDriver()), ids=lambda c: c.id)
def test_conformance_case(case) -> None:
    case.run()


def test_checkpoint_payload_carries_schema() -> None:
    driver = ${NAME_PASCAL}SubstrateDriver()
    # describe() has no context arg; prepare needs only a request for this driver.
    from vcs_core.spi import DriverContext
    from vcs_core.spi import SubstrateStoreIdentity

    ctx = DriverContext(
        operation_id="op-test",
        binding="${NAME_SNAKE}",
        role="${NAME_ROLE}",
        store_identity=SubstrateStoreIdentity(
            store_id="store_${NAME_SNAKE}", kind="acme.${NAME_SNAKE}", resource_id="${NAME_SNAKE}:test"
        ),
    )
    result = driver.prepare(ctx, CommandRequest(command="checkpoint", params={"payload": {"x": 1}}))
    assert result.transitions[0].payload == {"schema": ${NAME_SNAKE_UPPER}_REVISION_SCHEMA, "x": 1}
EOF

    cat > "$TARGET/README.md" <<EOF
# ${NAME_DASH}-substrate

An out-of-tree vcs-core substrate (SPI v0.1). See
\`vcs-core/design/guides/GUIDE-implementing-a-substrate.md\` **Path C** for the
full out-of-tree walkthrough.

## Develop

vcs-core is alpha and not yet on a registry, so first point \`[tool.uv.sources]\`
in \`pyproject.toml\` at your local checkout (see the commented hint there). Then:

\`\`\`bash
uv run pytest          # runs the conformance kit + behavioral tests
\`\`\`

## Use

Install this package alongside vcs-core, then bind the driver by name:

\`\`\`bash
vcs-core binding add ${NAME_SNAKE} acme.${NAME_SNAKE}
vcs-core sub ${NAME_SNAKE} checkpoint --help
vcs-core sub ${NAME_SNAKE} checkpoint --payload '{"x":1}'
\`\`\`

The driver is pure (it imports \`vcs_core.spi\` only). Durable *state-substrate
store installation* through \`WorldStorageManager\` is still in-tree wiring — if
this substrate needs durable revisions through the manager, coordinate first
(see Path C's limitation box).
EOF

    echo "Generated out-of-tree package: $TARGET"
    echo "  pyproject.toml (entry point: acme.${NAME_SNAKE})"
    echo "  src/$PKG/{driver,plugin}.py"
    echo "  tests/test_driver.py (conformance kit)"
    echo "  README.md"
    echo ""
    echo "Next steps:"
    echo "  1. Point pyproject's [tool.uv.sources] at your vcs-core checkout (it is alpha/unpublished),"
    echo "     then: cd $TARGET && uv run pytest    # the kit-based tests should pass against vcs-core"
    echo "  2. Customize ${NAME_PASCAL}SubstrateDriver's identity + commands."
    echo "  3. See GUIDE-implementing-a-substrate.md Path C for registration + the store-install limitation."
    exit 0
fi

# ===========================================================================
# In-tree mode — a new built-in under vcs-core core (Path A)
# ===========================================================================
PKG="$ROOT/packages/core/src/vcs_core"
TESTS="$ROOT/packages/core/tests/unit"

DRIVER_FILE="$PKG/_${NAME_SNAKE}_substrate.py"
TEST_FILE="$TESTS/test_${NAME_SNAKE}_substrate_driver.py"

if [ -e "$DRIVER_FILE" ]; then
    echo "error: $DRIVER_FILE already exists" >&2
    exit 2
fi
if [ -e "$TEST_FILE" ]; then
    echo "error: $TEST_FILE already exists" >&2
    exit 2
fi

cat > "$DRIVER_FILE" <<EOF
"""${NAME_PASCAL} substrate driver — SPI v0.1 Path A skeleton.

Generated by scripts/scaffold-substrate.sh; customize for your
substrate's actual semantics. See
vcs-core/design/guides/GUIDE-implementing-a-substrate.md (Path A)
for the full walkthrough.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, assert_never

from vcs_core.spi import (
    BaseSubstrateDriver,
    CapabilitySet,
    CaptureRequest,
    CommandRequest,
    CommandSpec,
    DriverContext,
    DriverIngressResult,
    DriverSchema,
    IngressRequest,
    MergeRequest,
    ParamSpec,
    ReduceRequest,
    RevisionStorageProfile,
    ScanRequest,
    TransitionDraft,
    UnsupportedRequestError,
)

# In-tree only: the generic role adapter wraps the private WorldStorageManager.
from vcs_core._world_storage_manager import PreparedCandidateBundle
from vcs_core._world_substrate_adapters import RoleSubstrateAdapter


${NAME_SNAKE_UPPER}_REVISION_SCHEMA = "shepherd/${NAME_DASH}-revision/v1"


@dataclass(frozen=True)
class ${NAME_PASCAL}SubstrateDriver(BaseSubstrateDriver):
    """JSON-backed ${NAME_DASH} substrate driver (scaffold)."""

    store_id: str = "store_${NAME_SNAKE}"
    binding: str = "${NAME_SNAKE}"
    role: str = "${NAME_ROLE}"
    driver_id: str = "shepherd.${NAME_SNAKE}"
    driver_version: str = "v1"
    materialization_class: str = "external"

    @property
    def capabilities(self) -> CapabilitySet:
        # Per SPI v0.1 §Result Shape "Capabilities are a runtime contract",
        # only include request types your prepare handler actually supports.
        return CapabilitySet(
            accepts=frozenset({CommandRequest}),
            selectable=True,
        )

    def describe(self) -> DriverSchema:
        return DriverSchema(
            driver_id=self.driver_id,
            driver_version=self.driver_version,
            capabilities=self.capabilities,
            storage_profile=RevisionStorageProfile(
                shape="json-snapshot",
                authority_role="authority",
                growth_bound="bounded",
            ),
            commands={
                "checkpoint": CommandSpec(
                    description="Snapshot current ${NAME_DASH} state.",
                    params={
                        "payload": ParamSpec(
                            type="object",
                            description="canonical ${NAME_DASH} payload",
                        ),
                    },
                ),
                "create-candidate": CommandSpec(
                    description="Create a ${NAME_DASH} candidate revision.",
                    params={
                        "payload": ParamSpec(type="object"),
                    },
                ),
            },
        )

    def prepare(
        self,
        context: DriverContext,
        request: IngressRequest,
    ) -> DriverIngressResult:
        match request:
            case CommandRequest(command="checkpoint", params=params):
                return self._prepare_state(context, "checkpoint", params)
            case CommandRequest(command="create-candidate", params=params):
                return self._prepare_state(context, "${NAME_DASH}-json-revision", params)
            case CommandRequest(command=other):
                raise ValueError(f"unsupported ${NAME_DASH} command: {other!r}")
            case ScanRequest() | CaptureRequest() | ReduceRequest() | MergeRequest():
                # capabilities only accepts CommandRequest; coordinator
                # rejects non-accepted variants. Defensive fallback for
                # callers that bypass the coordinator.
                raise UnsupportedRequestError(
                    driver_id=self.driver_id, request_type=type(request)
                )
            case _:
                # SPI v0.1 §Q1 exhaustiveness — required.
                assert_never(request)

    def _prepare_state(
        self,
        context: DriverContext,
        semantic_op: str,
        params: Mapping[str, Any],
    ) -> DriverIngressResult:
        del context
        payload_dict: dict[str, Any] = {"schema": ${NAME_SNAKE_UPPER}_REVISION_SCHEMA}
        inner = params.get("payload", {})
        if isinstance(inner, Mapping):
            payload_dict.update(inner)
        return DriverIngressResult(
            transitions=(
                TransitionDraft(
                    transition_id="primary",
                    semantic_op=semantic_op,
                    payload=payload_dict,
                    observation_ids=(),
                ),
            ),
        )


@dataclass(frozen=True)
class ${NAME_PASCAL}SubstrateAdapter(RoleSubstrateAdapter):
    """Role-aware helper for ${NAME_DASH} substrate revisions.

    Subclasses the generic RoleSubstrateAdapter: it supplies the driver and the
    payload-keyed sugar; the base supplies _context, manager bookkeeping, and
    the build_revision / build_candidate core.
    """

    driver: BaseSubstrateDriver = field(default_factory=${NAME_PASCAL}SubstrateDriver)

    def create_checkpoint(
        self,
        ref: str,
        payload: dict[str, Any],
        *,
        operation_id: str,
        parents: tuple[str, ...] = (),
        message: str | None = None,
    ) -> str:
        return self.build_revision(
            ref,
            operation_id=operation_id,
            params={"payload": payload},
            parents=parents,
            message=message,
        )

    def create_candidate(
        self,
        *,
        operation_id: str,
        payload: dict[str, Any],
        parents: tuple[str, ...] = (),
        message: str | None = None,
    ) -> PreparedCandidateBundle:
        return self.build_candidate(
            operation_id=operation_id,
            params={"payload": payload},
            parents=parents,
            message=message,
        )


__all__ = [
    "${NAME_SNAKE_UPPER}_REVISION_SCHEMA",
    "${NAME_PASCAL}SubstrateAdapter",
    "${NAME_PASCAL}SubstrateDriver",
]
EOF

cat > "$TEST_FILE" <<EOF
"""Unit tests for ${NAME_PASCAL}SubstrateDriver (scaffold)."""

from __future__ import annotations

import pytest
from vcs_core._${NAME_SNAKE}_substrate import (
    ${NAME_PASCAL}SubstrateDriver,
    ${NAME_SNAKE_UPPER}_REVISION_SCHEMA,
)
from vcs_core.spi import (
    CommandRequest,
    DriverContext,
    ScanRequest,
    SubstrateStoreIdentity,
    UnsupportedRequestError,
)
from vcs_core.spi.testing import assert_substrate_driver_conformant


def _context() -> DriverContext:
    return DriverContext(
        operation_id="op-test",
        binding="${NAME_SNAKE}",
        role="${NAME_ROLE}",
        store_identity=SubstrateStoreIdentity(
            store_id="store_${NAME_SNAKE}",
            kind="shepherd.${NAME_SNAKE}",
            resource_id="${NAME_SNAKE}:test",
        ),
    )


def test_driver_is_spi_conformant() -> None:
    # The exportable conformance kit covers structural / identity / describe /
    # dispatch / evidence-kind checks in one call.
    assert_substrate_driver_conformant(${NAME_PASCAL}SubstrateDriver())


def test_typed_dispatch_checkpoint() -> None:
    driver = ${NAME_PASCAL}SubstrateDriver()
    result = driver.prepare(
        _context(),
        CommandRequest(command="checkpoint", params={"payload": {"x": 1}}),
    )
    assert len(result.transitions) == 1
    assert result.transitions[0].semantic_op == "checkpoint"
    assert result.transitions[0].payload == {"schema": ${NAME_SNAKE_UPPER}_REVISION_SCHEMA, "x": 1}


def test_typed_dispatch_create_candidate() -> None:
    driver = ${NAME_PASCAL}SubstrateDriver()
    result = driver.prepare(
        _context(),
        CommandRequest(command="create-candidate", params={"payload": {"y": 2}}),
    )
    assert result.transitions[0].semantic_op == "${NAME_DASH}-json-revision"


def test_typed_dispatch_rejects_unknown_command() -> None:
    driver = ${NAME_PASCAL}SubstrateDriver()
    with pytest.raises(ValueError, match="unsupported ${NAME_DASH} command"):
        driver.prepare(
            _context(),
            CommandRequest(command="bogus", params={}),
        )


def test_typed_dispatch_rejects_non_command_request() -> None:
    driver = ${NAME_PASCAL}SubstrateDriver()
    with pytest.raises(UnsupportedRequestError):
        driver.prepare(_context(), ScanRequest(scan_kind="x"))
EOF

echo "Generated:"
echo "  $DRIVER_FILE"
echo "  $TEST_FILE"
echo ""
echo "Next steps:"
echo "  1. Edit ${NAME_PASCAL}SubstrateDriver's identity attributes if defaults don't fit."
echo "  2. Customize commands and parameters for your substrate's actual surface."
echo "  3. Wire the substrate store into WorldStorageManager at install time."
echo "  4. Add ${NAME_PASCAL}SubstrateDriver to:"
echo "     - tests/contract/conftest.py::DRIVERS_UNDER_TEST (opts into the conformance kit)"
echo "       and the expected set in test_capabilities_runtime_contract.py's inventory guard"
echo "     - tests/contract/test_spi_conformance.py::test_typed_dispatch_drivers_end_match_with_assert_never"
echo "       (the 'typed_dispatch_drivers' tuple, for the AST assert_never discipline)"
echo "  5. Run: cd packages/core && uv run --group typing mypy -p vcs_core"
echo "         uv run --group test pytest $TEST_FILE -v"
echo ""
echo "See vcs-core/design/guides/GUIDE-implementing-a-substrate.md (Path A) for full walkthrough."
