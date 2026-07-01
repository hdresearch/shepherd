"""Tests for the runtime API substrate client proxy."""

from __future__ import annotations

from typing import Any, cast

import pytest
from vcs_core._command_contract import compile_command_contract
from vcs_core.runtime_api import CommandExecutionOptions, substrate_client
from vcs_core.spi import CapabilitySet, CommandRequest, CommandSpec, DriverSchema, ParamSpec
from vcs_core.types import RecordedCommandOutcome, ScopeInfo


def _schema() -> DriverSchema:
    return DriverSchema(
        driver_id="runtime",
        driver_version="test",
        capabilities=CapabilitySet(accepts=frozenset({CommandRequest}), selectable=False),
        commands={
            "run": CommandSpec(
                description="Run a task.",
                params={
                    "task_body": ParamSpec(type="callable", required=False, projectable=False),
                    "task_id": ParamSpec(type="str", required=False),
                    "args": ParamSpec(type="object", required=False),
                },
                required_one_of=(("task_body", "task_id"),),
            ),
            "register": CommandSpec(description="Register a task.", projectable=False),
            "create-candidate": CommandSpec(
                description="Create a candidate.",
                params={"payload": ParamSpec(type="object")},
            ),
            "configure": CommandSpec(
                description="Configure a task.",
                params={
                    "task_id": ParamSpec(type="str", required=False),
                    "limit": ParamSpec(type="int", required=False, has_default=True, default=7),
                },
            ),
        },
    )


class _BindingSurface:
    def schema(self, name: str) -> DriverSchema:
        assert name == "runtime"
        return _schema()

    def resolve_driver(self, name: str) -> object:
        schema = self.schema(name)
        return _ResolvedBinding(schema=schema, binding_name=name)


class _ResolvedBinding:
    def __init__(self, *, schema: DriverSchema, binding_name: str) -> None:
        self.schema = schema
        self.command_contracts = {
            name: compile_command_contract(schema, name, binding_name=binding_name) for name in schema.commands
        }


class _FakeVcsCore:
    def __init__(self) -> None:
        self.binding_contracts = _BindingSurface()
        self.ground = ScopeInfo(name="ground", ref="refs/vcscore/ground", instance_id="ground", creation_oid="")
        self.calls: list[tuple[str, str, ScopeInfo, CommandExecutionOptions | None, dict[str, Any]]] = []

    def exec(
        self,
        binding_name: str,
        command: str,
        *,
        scope: ScopeInfo,
        execution_options: CommandExecutionOptions | None = None,
        **params: Any,
    ) -> RecordedCommandOutcome:
        self.calls.append((binding_name, command, scope, execution_options, params))
        return RecordedCommandOutcome(oids=("1234567890abcdef",), value={"ok": True})


def _client(mg: _FakeVcsCore, *, scope: ScopeInfo | None = None) -> Any:
    return substrate_client(cast("Any", mg), "runtime", scope=scope)


def test_substrate_client_invokes_schema_command_with_native_python_params() -> None:
    mg = _FakeVcsCore()

    outcome = _client(mg).run(task_body=lambda: "ok", args={"marker": "proxy"})

    assert outcome.oids == ("1234567890abcdef",)
    assert mg.calls[0][0:3] == ("runtime", "run", mg.ground)
    assert mg.calls[0][4]["args"] == {"marker": "proxy"}
    assert callable(mg.calls[0][4]["task_body"])


def test_substrate_client_enforces_required_one_of_as_xor() -> None:
    mg = _FakeVcsCore()

    with pytest.raises(ValueError, match="accepts only one of: task_body, task_id"):
        _client(mg).run(task_body=lambda: None, task_id="pkg:task")


def test_substrate_client_invokes_non_cli_projectable_schema_command() -> None:
    mg = _FakeVcsCore()

    outcome = _client(mg).register()

    assert outcome.value == {"ok": True}
    assert mg.calls[0][1] == "register"


def test_substrate_client_maps_underscore_attribute_to_dashed_command() -> None:
    mg = _FakeVcsCore()

    _client(mg).create_candidate(payload={"id": "candidate"})

    assert mg.calls[0][1] == "create-candidate"
    assert mg.calls[0][4] == {"payload": {"id": "candidate"}}


def test_substrate_client_uses_explicit_scope() -> None:
    mg = _FakeVcsCore()
    scope = ScopeInfo(name="task", ref="refs/vcscore/scopes/task", instance_id="task", creation_oid="")

    _client(mg, scope=scope).run(task_id="pkg:task")

    assert mg.calls[0][2] is scope


def test_substrate_client_applies_command_defaults() -> None:
    mg = _FakeVcsCore()

    _client(mg).configure(task_id="pkg:task")

    assert mg.calls[0][4] == {"task_id": "pkg:task", "limit": 7}


def test_substrate_client_passes_execution_options_separately() -> None:
    mg = _FakeVcsCore()
    options = CommandExecutionOptions(non_reversible_run=True)

    _client(mg).configure(task_id="pkg:task", execution_options=options)

    assert mg.calls[0][3] is options
    assert mg.calls[0][4] == {"task_id": "pkg:task", "limit": 7}
