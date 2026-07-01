from dataclasses import replace

import pytest

from shepherd_kernel_v3_reference.kernel import (
    KernelProgram,
    KernelProgramValidationError,
    PreparedKernelProgram,
    elaborate,
    elaborate_publication_experimental,
    prepare_kernel_program,
    run_kernel,
    validate_kernel_program,
)
from shepherd_kernel_v3_reference.kernel.ir import (
    BinderDef,
    HandlerEnvDef,
    HandlerInstallDef,
    KBind,
    KForward,
    KHandle,
    KPerform,
    KPure,
    SchemaDef,
)
from shepherd_kernel_v3_reference.kernel.readiness import (
    KernelProgramReadinessError,
    KernelProgramReadinessTier,
    require_kernel_program_readiness,
)
from shepherd_kernel_v3_reference.schemas import AnySchema
from shepherd_kernel_v3_reference.source.experimental import Forward
from shepherd_kernel_v3_reference.source.handlers import HandlerEnv, StaticHandlerInstall
from shepherd_kernel_v3_reference.source.syntax import Handle, Let, Lit, Perform, Return, Var


def test_validate_kernel_program_accepts_elaborated_program() -> None:
    program = Let(
        "x",
        Handle(
            Perform("eff.a", Lit(None)),
            HandlerEnv(
                (
                    StaticHandlerInstall(
                        effect_kind="eff.a",
                        handler_id="h.v1",
                        handled_result_schema=AnySchema(),
                        payload_name="_payload",
                        body=Return(Lit("handled")),
                    ),
                )
            ),
        ),
        Return(Var("x")),
    )
    kernel = elaborate(program)

    prepared = validate_kernel_program(kernel)

    assert isinstance(prepared, PreparedKernelProgram)
    assert prepared.program.root == kernel.root
    assert prepared.program.binders is not kernel.binders


def test_validate_kernel_program_rejects_missing_binder_ref() -> None:
    kernel = KernelProgram(
        root=KBind(KPure(Lit(1)), "binder:missing", "env:0"),
        binders={},
        handler_envs={},
        schemas={},
    )

    with pytest.raises(KernelProgramValidationError, match="missing binder"):
        validate_kernel_program(kernel)


def test_validate_kernel_program_accepts_prepared_program_without_repreparing(monkeypatch: pytest.MonkeyPatch) -> None:
    prepared = prepare_kernel_program(elaborate(Return(Lit("done"))))

    def blocked_prepare(*args: object, **kwargs: object) -> object:
        raise AssertionError("prepared program was admitted again")

    monkeypatch.setattr(
        "shepherd_kernel_v3_reference.kernel.program_admission.prepare_kernel_program",
        blocked_prepare,
    )

    assert validate_kernel_program(prepared) is prepared


def test_validate_kernel_program_rejects_binder_env_mismatch() -> None:
    kernel = elaborate(Let("x", Return(Lit(1)), Return(Var("x"))))
    assert isinstance(kernel.root, KBind)
    malformed = KernelProgram(
        root=replace(kernel.root, binder_env_ref="env:wrong"),
        binders=kernel.binders,
        handler_envs=kernel.handler_envs,
        schemas=kernel.schemas,
    )

    with pytest.raises(KernelProgramValidationError, match="binder_env_ref"):
        validate_kernel_program(malformed)


def test_validate_kernel_program_rejects_missing_handler_env() -> None:
    kernel = KernelProgram(
        root=KHandle(KPure(Lit(1)), "handler-env:missing"),
        binders={},
        handler_envs={},
        schemas={},
    )

    with pytest.raises(KernelProgramValidationError, match="missing handler env"):
        validate_kernel_program(kernel)


def test_validate_kernel_program_rejects_handler_env_key_mismatch() -> None:
    kernel = KernelProgram(
        root=KPure(Lit(1)),
        binders={},
        handler_envs={"handler-env:0": HandlerEnvDef("handler-env:wrong", ())},
        schemas={},
    )

    with pytest.raises(KernelProgramValidationError, match="handler-env map key"):
        validate_kernel_program(kernel)


def test_validate_kernel_program_rejects_schema_key_mismatch() -> None:
    kernel = KernelProgram(
        root=KPure(Lit(1)),
        binders={},
        handler_envs={},
        schemas={"schema:0": SchemaDef("schema:wrong", AnySchema())},
    )

    with pytest.raises(KernelProgramValidationError, match="schema map key"):
        validate_kernel_program(kernel)


def test_kernel_evaluator_runs_preflight_before_execution() -> None:
    malformed = KernelProgram(
        root=KPerform("eff.a", Lit(None), payload_schema_ref="schema:missing"),
        binders={},
        handler_envs={},
        schemas={},
    )

    with pytest.raises(KernelProgramValidationError, match="schema:missing"):
        run_kernel(malformed)


def test_core_kernel_program_rejects_publication_control_ir() -> None:
    kernel = KernelProgram(root=KForward(), binders={}, handler_envs={}, schemas={})

    with pytest.raises(
        KernelProgramValidationError,
        match="publication experimental profile",
    ):
        validate_kernel_program(kernel)


def test_publication_profile_kernel_program_accepts_publication_control_ir() -> None:
    term = Handle(
        Perform("eff.a", Lit(None)),
        HandlerEnv(
            (
                StaticHandlerInstall(
                    effect_kind="eff.a",
                    handler_id="h.v1",
                    handled_result_schema=AnySchema(),
                    payload_name="_payload",
                    body=Forward(),
                ),
            )
        ),
    )

    validate_kernel_program(elaborate_publication_experimental(term))


def test_validate_kernel_program_admits_deep_left_nested_controls() -> None:
    validate_kernel_program(_left_nested_program(1200))


def test_program_readiness_distinguishes_admission_from_identity_projection() -> None:
    kernel = _left_nested_program(1200)

    readiness = require_kernel_program_readiness(kernel, KernelProgramReadinessTier.ADMITTED)

    assert readiness.tier == KernelProgramReadinessTier.ADMITTED
    assert readiness.program_ref is None
    with pytest.raises(KernelProgramReadinessError, match="identity_ready"):
        require_kernel_program_readiness(kernel, KernelProgramReadinessTier.IDENTITY_READY)


def test_program_identity_readiness_returns_program_ref_for_shallow_program() -> None:
    readiness = require_kernel_program_readiness(
        KernelProgram(root=KPure(Lit("ok")), binders={}, handler_envs={}, schemas={}),
        KernelProgramReadinessTier.IDENTITY_READY,
    )

    assert readiness.program_ref is not None
    assert readiness.program_ref.startswith("program:sha256:")


def test_validate_kernel_program_rejects_deep_invalid_control_without_recursion_error() -> None:
    kernel = _left_nested_program(1200, deepest_binder_id="binder:missing")

    with pytest.raises(KernelProgramValidationError, match="missing binder"):
        validate_kernel_program(kernel)


def test_validate_kernel_program_rejects_very_deep_invalid_control_with_bounded_diagnostic() -> None:
    kernel = _left_nested_program(5000, deepest_binder_id="binder:missing")

    with pytest.raises(KernelProgramValidationError) as exc_info:
        validate_kernel_program(kernel)

    message = str(exc_info.value)
    assert "missing binder" in message
    assert "depth=" in message
    assert len(message) < 4096


def test_validate_kernel_program_rejects_unused_binder_identity_cycle() -> None:
    kernel = KernelProgram(
        root=KPure(Lit("ok")),
        binders={
            "binder:cycle": BinderDef(
                binder_id="binder:cycle",
                param_name="x",
                body=KBind(KPure(Lit("bound")), "binder:cycle", "env:cycle"),
                binder_env_ref="env:cycle",
            )
        },
        handler_envs={},
        schemas={},
    )

    with pytest.raises(KernelProgramValidationError, match="identity dependency cycle"):
        validate_kernel_program(kernel)


def test_validate_kernel_program_rejects_unused_handler_install_identity_cycle() -> None:
    install = HandlerInstallDef(
        install_ref="install:cycle",
        effect_kind="eff.a",
        handler_id="h.cycle",
        handled_result_schema_ref="schema:any",
        payload_name="_payload",
        body=KHandle(KPure(Lit("handled")), "handler-env:cycle"),
    )
    kernel = KernelProgram(
        root=KPure(Lit("ok")),
        binders={},
        handler_envs={"handler-env:cycle": HandlerEnvDef("handler-env:cycle", (install,))},
        schemas={"schema:any": SchemaDef("schema:any", AnySchema())},
    )

    with pytest.raises(KernelProgramValidationError, match="identity dependency cycle"):
        validate_kernel_program(kernel)


def test_kernel_evaluator_uses_admission_for_unused_cycles() -> None:
    kernel = KernelProgram(
        root=KPure(Lit("ok")),
        binders={
            "binder:cycle": BinderDef(
                binder_id="binder:cycle",
                param_name="x",
                body=KBind(KPure(Lit("bound")), "binder:cycle", "env:cycle"),
                binder_env_ref="env:cycle",
            )
        },
        handler_envs={},
        schemas={},
    )

    with pytest.raises(KernelProgramValidationError, match="identity dependency cycle"):
        run_kernel(kernel)


def _left_nested_program(depth: int, *, deepest_binder_id: str | None = None) -> KernelProgram:
    binders: dict[str, BinderDef] = {}
    control: object = KPure(Lit("leaf"))
    for idx in range(depth):
        binder_id = f"binder:{idx}"
        binder_env_ref = f"env:{idx}"
        binders[binder_id] = BinderDef(
            binder_id=binder_id,
            param_name=f"x{idx}",
            body=KPure(Lit(idx)),
            binder_env_ref=binder_env_ref,
        )
        cited_binder_id = deepest_binder_id if idx == 0 and deepest_binder_id is not None else binder_id
        control = KBind(control, cited_binder_id, binder_env_ref)
    assert isinstance(control, KBind)
    return KernelProgram(
        root=control,
        binders=binders,
        handler_envs={},
        schemas={},
    )
