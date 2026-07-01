"""Conformance kit for ``SubstrateDriver`` implementations.

The checks the built-in drivers are held to, packaged so **out-of-tree**
drivers get the same coverage (``decisions.md`` ``substrate-conformance-kit``).
The core contract tests consume this module too, so there is one source of
truth rather than per-driver factories hand-rolled in the test suite.

Pytest-free by design: this module ships inside the runtime wheel, so it must
not import ``pytest``. The aggregate ``assert_substrate_driver_conformant``
raises ``AssertionError`` directly; ``conformance_cases(driver)`` returns plain
named cases a caller parametrizes (``@pytest.mark.parametrize(... , ids=...)``)
without the kit ever touching a test framework.

Usage (out-of-tree, in your own test module)::

    from vcs_core.spi.testing import assert_substrate_driver_conformant, conformance_cases
    import pytest


    def test_my_driver_conformant():
        assert_substrate_driver_conformant(MyDriver())


    @pytest.mark.parametrize("case", conformance_cases(MyDriver()), ids=lambda c: c.id)
    def test_my_driver_conformance_case(case):
        case.run()

What the aggregate checks: structural ``isinstance(driver, SubstrateDriver)``;
driver-identity validity; ``describe()`` coherence (its ``driver_id`` /
``driver_version`` / ``capabilities`` match the driver's); the
capabilities-as-runtime-contract rule (every accepted ``IngressRequest``
variant dispatches without ``UnsupportedRequestError`` / ``NotImplementedError``);
the Q4 evidence-kind reconciliation for each driver-default capture adapter; and
— iff the driver opts into execution (``ExecutionBoundDriver``) — the fail-closed
negotiation rule via :func:`verify_execution_negotiation`.

The ``match … assert_never`` exhaustiveness discipline is **opt-in**
(:func:`assert_match_dispatch_exhaustive`), never part of the aggregate: it is a
house style for drivers that dispatch via ``match request:``, and a driver that
dispatches with ``if`` / ``raise`` (e.g. an execution driver whose ``run`` arm
refuses early) is fully conformant without it.
"""

from __future__ import annotations

import ast
import functools
import inspect
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from vcs_core._driver_schema_validation import (
    DriverSchemaValidationError,
    validate_driver_schema,
    validate_projectable_command,
)
from vcs_core._errors import InvalidRepositoryStateError

# Two helpers the kit needs that are deliberately NOT on the public SPI:
# ReductionBatch is a private reducer-support record; validate_driver_identity
# is the lower-level identity validator. The kit lives inside vcs_core, so the
# in-tree private import is fine.
from vcs_core._substrate_driver import ReductionBatch, validate_driver_identity
from vcs_core.spi import (
    CaptureRequest,
    CommandRequest,
    DriverAuthorityRequiredError,
    DriverContext,
    DriverIngressResult,
    ExecutionAuthorityRequired,
    ExecutionBoundDriver,
    IngressRequest,
    MergeRequest,
    ParamSpec,
    ReduceRequest,
    ScanRequest,
    SubstrateDriver,
    SubstrateStoreIdentity,
    UnsupportedRequestError,
    validate_driver_ingress,
    verify_execution_negotiation,
)

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "ConformanceCase",
    "assert_execution_driver_conformant",
    "assert_match_dispatch_exhaustive",
    "assert_substrate_driver_conformant",
    "build_probe_context",
    "conformance_cases",
]

# The v0.1 mechanism-prefixed evidence_kind convention: ``<mechanism>:<kind>``,
# both sides lowercase alnum/hyphen/underscore (see SPI doc §Q2). Lifted from
# test_evidence_kind_reconciliation.py so the rule has one home.
_EVIDENCE_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]*:[a-z][a-z0-9_-]*$")


# ---------------------------------------------------------------------------
# Probe context
# ---------------------------------------------------------------------------


def build_probe_context(
    driver: SubstrateDriver,
    *,
    binding: str | None = None,
    role: str | None = None,
    store_id: str | None = None,
) -> DriverContext:
    """A minimal :class:`DriverContext` for exercising ``driver.prepare``.

    ``binding`` / ``role`` / ``store_id`` are read from the driver when present
    (the ``BaseSubstrateDriver`` convention) and otherwise default — the
    ``SubstrateDriver`` Protocol requires none of them, so a bare Protocol
    implementation still gets a usable context. Override any of them by keyword.
    """
    resolved_binding = binding or getattr(driver, "binding", None) or "probe"
    resolved_role = role or getattr(driver, "role", None) or f"probe.{resolved_binding}"
    resolved_store = store_id or getattr(driver, "store_id", None) or f"store_{resolved_binding}"
    return DriverContext(
        operation_id="op_spi_conformance_probe",
        binding=resolved_binding,
        role=resolved_role,
        store_identity=SubstrateStoreIdentity(
            store_id=resolved_store,
            kind=f"probe.{resolved_binding}",
            resource_id=f"{resolved_binding}:probe",
        ),
    )


# ---------------------------------------------------------------------------
# Per-variant request factories (generalized from the contract suite)
#
# Each builds a minimal valid request of its variant, choosing an operation the
# driver declares in describe() so dispatch reaches a real handler rather than
# the driver's defensive "unsupported" arm. Where the driver declares nothing
# for a variant it accepts, a neutral probe value still exercises dispatch.
# ---------------------------------------------------------------------------


def _make_command_request(driver: SubstrateDriver) -> CommandRequest:
    commands = driver.describe().commands
    command_name = next(iter(commands), "probe")
    return CommandRequest(command=command_name, params={"payload": {}})


def _make_scan_request(driver: SubstrateDriver) -> ScanRequest:
    scans = driver.describe().scans
    scan_kind = next(iter(scans), "probe")
    return ScanRequest(scan_kind=scan_kind, external_state={"payload": {}})


def _make_capture_request(driver: SubstrateDriver) -> CaptureRequest:
    adapters = driver.describe().capture_adapters
    adapter_id = adapters[0].adapter_id if adapters else "probe:adapter"
    return CaptureRequest(adapter_id=adapter_id, observations=())


def _make_reduce_request(driver: SubstrateDriver) -> ReduceRequest:
    del driver
    return ReduceRequest(evidence_citations=ReductionBatch(citations=()))


def _make_merge_request(driver: SubstrateDriver) -> MergeRequest:
    policy: dict[str, object] = {"payload": {}}
    merges = driver.describe().merges
    if merges:
        policy["merge_kind"] = next(iter(merges))
    return MergeRequest(other_head="0" * 40, policy=policy)


_FACTORIES: dict[type[IngressRequest], Callable[[SubstrateDriver], IngressRequest]] = {
    CommandRequest: _make_command_request,
    ScanRequest: _make_scan_request,
    CaptureRequest: _make_capture_request,
    ReduceRequest: _make_reduce_request,
    MergeRequest: _make_merge_request,
}


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _check_structural(driver: SubstrateDriver) -> None:
    assert isinstance(driver, SubstrateDriver), (
        f"{type(driver).__name__} does not structurally satisfy the SubstrateDriver "
        f"Protocol (missing one of driver_id / driver_version / capabilities / "
        f"describe / prepare / capture_adapters / validate_result)."
    )


def _check_identity(driver: SubstrateDriver) -> None:
    try:
        validate_driver_identity(driver_id=driver.driver_id, driver_version=driver.driver_version)
    except InvalidRepositoryStateError as exc:
        raise AssertionError(f"{type(driver).__name__} identity is not record-safe: {exc}") from exc


def _check_describe_coherence(driver: SubstrateDriver) -> None:
    schema = driver.describe()
    assert schema.driver_id == driver.driver_id, (
        f"{type(driver).__name__}.describe().driver_id ({schema.driver_id!r}) "
        f"disagrees with driver.driver_id ({driver.driver_id!r})."
    )
    assert schema.driver_version == driver.driver_version, (
        f"{type(driver).__name__}.describe().driver_version ({schema.driver_version!r}) "
        f"disagrees with driver.driver_version ({driver.driver_version!r})."
    )
    assert schema.capabilities == driver.capabilities, (
        f"{type(driver).__name__}.describe().capabilities disagrees with "
        f"driver.capabilities; describe() must report the live capability set."
    )


def _check_describe_context_invariance(driver: SubstrateDriver) -> None:
    schema = driver.describe()
    # ``describe()`` has no context argument in v0.1. Build distinct probe
    # contexts anyway so a future accidental context/readiness coupling has a
    # named conformance case to trip over.
    build_probe_context(driver, binding="probe_a", role="probe.a", store_id="store_probe_a")
    build_probe_context(driver, binding="probe_b", role="probe.b", store_id="store_probe_b")
    assert driver.describe() == schema, (
        f"{type(driver).__name__}.describe() changed across probe contexts; "
        "v0.1 projection metadata must be context-invariant."
    )


def _check_schema_validity(driver: SubstrateDriver) -> None:
    try:
        validate_driver_schema(driver.describe())
    except DriverSchemaValidationError as exc:
        raise AssertionError(f"{type(driver).__name__}.describe() schema is invalid: {exc}") from exc


def _check_projectability(driver: SubstrateDriver) -> None:
    schema = driver.describe()
    try:
        validate_driver_schema(schema)
        for command_name in schema.commands:
            validate_projectable_command(schema, command_name)
    except DriverSchemaValidationError as exc:
        raise AssertionError(f"{type(driver).__name__}.describe() projectability is invalid: {exc}") from exc


def _check_schema_anti_inference(driver: SubstrateDriver) -> None:
    """Non-projectable annotations must not alter command projection semantics."""
    schema = driver.describe()
    try:
        validate_driver_schema(schema)
    except DriverSchemaValidationError as exc:
        raise AssertionError(f"{type(driver).__name__}.describe() schema is invalid: {exc}") from exc
    for command_name, command_spec in schema.commands.items():
        try:
            baseline = validate_projectable_command(schema, command_name)
        except DriverSchemaValidationError as exc:
            raise AssertionError(f"{type(driver).__name__}.describe() projectability is invalid: {exc}") from exc
        probe_param_name = "__meta_probe"
        while probe_param_name in command_spec.params:
            probe_param_name += "_"
        augmented_command = replace(
            command_spec,
            params={
                **command_spec.params,
                probe_param_name: ParamSpec(
                    type="PythonOnlyAnnotation",
                    required=False,
                    projectable=False,
                    description="Synthetic conformance probe; never dispatched.",
                ),
            },
        )
        augmented_schema = replace(
            schema,
            commands={**schema.commands, command_name: augmented_command},
        )
        try:
            augmented = validate_projectable_command(augmented_schema, command_name)
        except DriverSchemaValidationError as exc:
            raise AssertionError(
                f"{type(driver).__name__}.describe() non-projectable annotation probe failed: {exc}"
            ) from exc
        assert augmented.projectable == baseline.projectable, (
            f"{type(driver).__name__}.describe() command {command_name!r}: adding a non-projectable "
            "annotation changed command projectability."
        )
        assert augmented.projectable_params == baseline.projectable_params, (
            f"{type(driver).__name__}.describe() command {command_name!r}: adding a non-projectable "
            "annotation changed projected params."
        )
        assert augmented.command_reasons == baseline.command_reasons, (
            f"{type(driver).__name__}.describe() command {command_name!r}: adding a non-projectable "
            "annotation changed command projection reasons."
        )


def _check_accepted_variant_dispatches(
    driver: SubstrateDriver,
    request_type: type[IngressRequest],
    *,
    context: DriverContext,
) -> None:
    """The capabilities-as-runtime-contract rule for one accepted variant.

    Dispatching a minimal valid request of an accepted variant must not raise
    ``UnsupportedRequestError`` / ``NotImplementedError`` (those mean the
    handler doesn't exist). Domain rejections (``ValueError`` /
    ``InvalidRepositoryStateError`` / ``ExecutionAuthorityRequired``) are fine
    — the handler ran. A returned ``DriverIngressResult`` is re-validated.
    """
    factory = _FACTORIES.get(request_type)
    assert factory is not None, (
        f"{type(driver).__name__} accepts {request_type.__name__}, which the "
        f"conformance kit has no factory for; the kit must grow a factory in "
        f"lockstep with any new IngressRequest variant."
    )
    request = factory(driver)
    try:
        result = driver.prepare(context, request)
    except UnsupportedRequestError as exc:
        raise AssertionError(
            f"{type(driver).__name__}.capabilities.accepts advertises "
            f"{request_type.__name__} but prepare() raised UnsupportedRequestError: {exc}. "
            f"Aspirational entries in accepts are contract violations."
        ) from exc
    except NotImplementedError as exc:
        raise AssertionError(
            f"{type(driver).__name__}.capabilities.accepts advertises "
            f"{request_type.__name__} but the handler raised NotImplementedError: {exc}. "
            f"Wire the handler in the same change that adds the type to accepts."
        ) from exc
    except (ValueError, InvalidRepositoryStateError, DriverAuthorityRequiredError, ExecutionAuthorityRequired):
        # The handler ran and rejected the minimal request on domain grounds —
        # that is conformant; the contract failures are the two raises above.
        return
    assert isinstance(result, DriverIngressResult), (
        f"{type(driver).__name__}.prepare({request_type.__name__}) returned "
        f"{type(result).__name__}, expected DriverIngressResult."
    )
    # Re-validate through the full three-layer validator (incl. the driver's
    # own validate_result) — a returned result must satisfy the SPI invariants.
    validate_driver_ingress(request, result, driver)


def _check_adapter_evidence_kinds(driver: SubstrateDriver, *, context: DriverContext) -> None:
    """Q4 reconciliation for each driver-default capture adapter.

    Each adapter's ``evidence_kinds`` must match its ``describe()`` row and
    follow the mechanism-prefixed convention.
    """
    schema = driver.describe()
    for adapter in driver.capture_adapters(context):
        matching = [s for s in schema.capture_adapters if s.adapter_id == adapter.adapter_id]
        assert len(matching) == 1, (
            f"{type(driver).__name__}.describe().capture_adapters must contain exactly one "
            f"row for adapter_id={adapter.adapter_id!r}; found {len(matching)}."
        )
        adapter_kinds = set(adapter.evidence_kinds)
        schema_kinds = set(matching[0].evidence_kinds)
        assert adapter_kinds == schema_kinds, (
            f"{type(driver).__name__} adapter {adapter.adapter_id!r}: evidence_kinds "
            f"{sorted(adapter_kinds)} disagree with describe() {sorted(schema_kinds)}."
        )
        expected_prefix = f"{adapter.mechanism}:"
        for evidence_kind in adapter.evidence_kinds:
            assert _EVIDENCE_KIND_RE.match(evidence_kind), (
                f"{type(driver).__name__} adapter {adapter.adapter_id!r}: evidence_kind "
                f"{evidence_kind!r} violates the <mechanism>:<kind> convention."
            )
            assert evidence_kind.startswith(expected_prefix), (
                f"{type(driver).__name__} adapter {adapter.adapter_id!r}: evidence_kind "
                f"{evidence_kind!r} does not match the adapter mechanism prefix "
                f"{expected_prefix!r}."
            )


def _check_execution_negotiation(driver: SubstrateDriver) -> None:
    """Iff the driver opts into execution, the fail-closed negotiation rule."""
    if isinstance(driver, ExecutionBoundDriver):
        verify_execution_negotiation(driver)


# ---------------------------------------------------------------------------
# Aggregates + case list
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConformanceCase:
    """One named conformance check; ``run()`` raises ``AssertionError`` on failure.

    A caller parametrizes over ``conformance_cases(driver)`` and invokes
    ``case.run()`` — the kit never imports a test framework.
    """

    id: str
    run: Callable[[], None]


def conformance_cases(driver: SubstrateDriver) -> tuple[ConformanceCase, ...]:
    """The conformance checks for ``driver`` as discrete, named cases.

    Stable ids: ``structural``, ``identity``, ``describe_coherence``,
    ``describe_context_invariance``, ``schema_validity``, ``projectability``,
    ``schema_anti_inference``, ``dispatch:<RequestType>`` per accepted variant,
    ``evidence_kinds``, and ``execution_negotiation`` when the driver opts into
    execution.
    """
    context = build_probe_context(driver)
    cases: list[ConformanceCase] = [
        ConformanceCase("structural", lambda: _check_structural(driver)),
        ConformanceCase("identity", lambda: _check_identity(driver)),
        ConformanceCase("describe_coherence", lambda: _check_describe_coherence(driver)),
        ConformanceCase("describe_context_invariance", lambda: _check_describe_context_invariance(driver)),
        ConformanceCase("schema_validity", lambda: _check_schema_validity(driver)),
        ConformanceCase("projectability", lambda: _check_projectability(driver)),
        ConformanceCase("schema_anti_inference", lambda: _check_schema_anti_inference(driver)),
    ]
    # One dispatch case per accepted variant (stable, sorted by type name).
    # functools.partial binds the loop variable by value (a bare closure would
    # capture the final value) and types cleanly as a zero-arg callable.
    for request_type in sorted(driver.capabilities.accepts, key=lambda t: t.__name__):
        cases.append(
            ConformanceCase(
                f"dispatch:{request_type.__name__}",
                functools.partial(_check_accepted_variant_dispatches, driver, request_type, context=context),
            )
        )
    cases.append(ConformanceCase("evidence_kinds", lambda: _check_adapter_evidence_kinds(driver, context=context)))
    if isinstance(driver, ExecutionBoundDriver):
        cases.append(ConformanceCase("execution_negotiation", lambda: _check_execution_negotiation(driver)))
    return tuple(cases)


def assert_substrate_driver_conformant(
    driver: SubstrateDriver,
    *,
    context: DriverContext | None = None,
) -> None:
    """Assert ``driver`` satisfies the SPI v0.1 conformance contract.

    Runs every check in :func:`conformance_cases`. Pass ``context`` to override
    the probe context (e.g. to pin a specific binding/store identity).
    """
    if context is None:
        cases = conformance_cases(driver)
        for case in cases:
            case.run()
        return
    # Context override: re-run the context-dependent checks against it.
    _check_structural(driver)
    _check_identity(driver)
    _check_describe_coherence(driver)
    _check_describe_context_invariance(driver)
    _check_schema_validity(driver)
    _check_projectability(driver)
    _check_schema_anti_inference(driver)
    for request_type in sorted(driver.capabilities.accepts, key=lambda t: t.__name__):
        _check_accepted_variant_dispatches(driver, request_type, context=context)
    _check_adapter_evidence_kinds(driver, context=context)
    _check_execution_negotiation(driver)


def assert_execution_driver_conformant(driver: SubstrateDriver) -> None:
    """Assert an execution-bound driver conforms, including the negotiation rule.

    The kit's self-check idiom as a one-liner: structural SPI conformance, the
    ``ExecutionBoundDriver`` opt-in, and the fail-closed negotiation rule. Use
    in a test to turn a driver module's bottom-of-file ``assert isinstance(...)``
    self-checks into suite-visible coverage.
    """
    assert isinstance(driver, SubstrateDriver), (
        f"{type(driver).__name__} does not satisfy the SubstrateDriver Protocol."
    )
    assert isinstance(driver, ExecutionBoundDriver), (
        f"{type(driver).__name__} does not opt into execution (no prepare_bound / "
        f"execution_commands); use assert_substrate_driver_conformant for plain drivers."
    )
    verify_execution_negotiation(driver)
    assert_substrate_driver_conformant(driver)


def assert_match_dispatch_exhaustive(driver_cls: type[SubstrateDriver]) -> None:
    """Opt-in house-style check: ``prepare`` ends its ``match`` with ``assert_never``.

    For drivers that dispatch via ``match request:`` over ``IngressRequest``,
    every match must end with ``case _: assert_never(request)`` so a future
    variant produces one mypy error per under-implementing driver rather than a
    silent fall-through. NOT part of the conformance aggregate — drivers that
    dispatch with ``if`` / ``raise`` are conformant without it.
    """
    source = inspect.getsource(driver_cls.prepare)
    tree = ast.parse(source.lstrip())
    match_nodes = [node for node in ast.walk(tree) if isinstance(node, ast.Match)]
    assert match_nodes, (
        f"{driver_cls.__name__}.prepare contains no `match request:` block; "
        f"assert_match_dispatch_exhaustive only applies to match-dispatch drivers."
    )
    for match in match_nodes:
        last = match.cases[-1]
        is_wildcard = isinstance(last.pattern, ast.MatchAs) and last.pattern.pattern is None
        assert is_wildcard, f"{driver_cls.__name__}.prepare match must end with a wildcard `case _:` arm."
        assert any(
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Name)
            and stmt.value.func.id == "assert_never"
            for stmt in last.body
        ), f"{driver_cls.__name__}.prepare wildcard arm must call `assert_never(request)`."
