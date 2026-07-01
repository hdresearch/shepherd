"""Workspace-wide pytest configuration for the shepherd packages.

Registers and loads a Hypothesis profile tuned for running under pytest-xdist.
With `-n auto` every CPU is saturated, so Hypothesis' ``too_slow`` health check
(it times input generation) and the per-example ``deadline`` fire on draws that
are perfectly fine when run in isolation. Both are timing artifacts of parallel
execution rather than invariant violations, so we relax them here. This conftest
is an ancestor of every shepherd package test, so the profile loads once on each
xdist worker process before any property test runs.
"""

import pytest
from hypothesis import HealthCheck, settings

settings.register_profile(
    "shepherd",
    suppress_health_check=[HealthCheck.too_slow],
    deadline=None,
)
settings.load_profile("shepherd")


@pytest.fixture(autouse=True)
def _isolate_global_runtime_registries():
    """Snapshot and restore shepherd_runtime's process-global registries per test.

    Several runtime tests reset or clear the global sandbox / materializer
    registries (``reset_default_registry``, ``clear_materializer_registry``) in
    fixtures or test bodies. Those registries are process-global, so under
    pytest-xdist — where unrelated test modules share a worker process — a reset
    can wipe an *import-time* registration that a later module on the same worker
    relies on (e.g. SimpleWorkspace's sandbox factory, WorkspaceRef's
    materializer). In a serial run this is masked by collection order; xdist
    surfaces it as order-dependent failures.

    We snapshot the registries before each test and restore them afterward, so no
    test's mutation leaks past its own boundary. Baseline (import-time)
    registrations are preserved; only in-test mutations are rolled back. Lazy,
    best-effort imports keep this harmless for packages that never load
    shepherd_runtime.
    """
    restores = []

    try:
        import shepherd_runtime.sandbox_registry as _sandbox
    except ImportError:
        _sandbox = None
    if _sandbox is not None:
        _reg = _sandbox._default_registry
        _factories = dict(_reg._factories) if _reg is not None else None

        def _restore_sandbox(reg=_reg, factories=_factories, mod=_sandbox):
            mod._default_registry = reg
            if reg is not None and factories is not None:
                reg._factories.clear()
                reg._factories.update(factories)

        restores.append(_restore_sandbox)

    try:
        import shepherd_runtime.materialization as _mat
    except ImportError:
        _mat = None
    if _mat is not None:
        _mats = dict(_mat._CONTEXT_MATERIALIZER_REGISTRY)
        _hooks = list(_mat._MATERIALIZATION_ADMISSION_HOOKS)

        def _restore_mat(mats=_mats, hooks=_hooks, mod=_mat):
            mod._CONTEXT_MATERIALIZER_REGISTRY.clear()
            mod._CONTEXT_MATERIALIZER_REGISTRY.update(mats)
            mod._MATERIALIZATION_ADMISSION_HOOKS[:] = hooks

        restores.append(_restore_mat)

    yield

    for restore in restores:
        restore()
