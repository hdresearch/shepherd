from __future__ import annotations

from typing import TYPE_CHECKING

from shepherd2.runtime.handles import Run as RuntimeRun

from shepherd2 import (
    TRUSTED_READ_CONTEXT,
    ReadContext,
    Run,
    SQLiteTraceStore,
    TaskControl,
    project_effective_history_from_store,
    project_execution,
    task,
)

READER = ReadContext(actor_ref="reader")

if TYPE_CHECKING:
    from pathlib import Path


def test_task_start_wait_projects_successful_execution() -> None:
    @task
    class AddOne:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self) -> dict[str, int]:
            return {"y": self.x + 1}

    store = SQLiteTraceStore()
    run = AddOne.start(store=store, run_id="run:add-one", x=10)  # type: ignore[attr-defined]
    execution = run.wait()

    assert isinstance(run, Run)
    assert RuntimeRun is Run
    assert execution.execution_id == run.execution_id
    assert execution.status == "succeeded"
    assert execution.inputs == {"x": 10}
    assert execution.outputs == {"y": 11}
    assert execution.task_ref.endswith("AddOne")


def test_failed_task_retains_failed_terminal_execution() -> None:
    @task
    class Fails:
        def execute(self) -> None:
            raise RuntimeError("boom")

    run = Fails.start(run_id="run:fails")  # type: ignore[attr-defined]
    execution = run.wait()

    assert execution.status == "failed"
    assert execution.outputs == {}
    assert execution.error == "RuntimeError: boom"


def test_completed_run_id_is_not_reexecuted() -> None:
    calls = 0

    @task
    class CountCalls:
        def execute(self) -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"calls": calls}

    store = SQLiteTraceStore()
    first = CountCalls.start(store=store, run_id="run:count-calls")  # type: ignore[attr-defined]
    second = CountCalls.start(store=store, run_id="run:count-calls")  # type: ignore[attr-defined]

    assert calls == 1
    assert second.execution_id == first.execution_id
    assert second.wait().outputs == {"calls": 1}


def test_terminal_run_can_be_projected_after_restart(tmp_path: Path) -> None:
    @task
    class Double:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self) -> dict[str, int]:
            return {"y": self.x * 2}

    db_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(db_path) as store:
        run = Double.start(store=store, run_id="run:double", x=21)  # type: ignore[attr-defined]
        frontier_id = run.frontier_id

    with SQLiteTraceStore(db_path) as restarted:
        cutoff = restarted.read_owner_cutoff(frontier_id)
        trace_slice = restarted.resolve_frontier(READER, cutoff.frontier_id)
        execution = project_execution(trace_slice, cutoff.target_trace_owner_id, cutoff=cutoff)

    assert execution.status == "succeeded"
    assert execution.inputs == {"x": 21}
    assert execution.outputs == {"y": 42}


def test_completed_run_id_is_not_reexecuted_after_restart(tmp_path: Path) -> None:
    calls = 0

    @task
    class CountCalls:
        def execute(self) -> dict[str, int]:
            nonlocal calls
            calls += 1
            return {"calls": calls}

    db_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(db_path) as store:
        CountCalls.start(store=store, run_id="run:count-calls-restart")  # type: ignore[attr-defined]

    with SQLiteTraceStore(db_path) as restarted:
        run = CountCalls.start(store=restarted, run_id="run:count-calls-restart")  # type: ignore[attr-defined]
        assert run.wait().outputs == {"calls": 1}

    assert calls == 1


def test_task_control_spawn_records_effective_child_history() -> None:
    @task
    class AddOne:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self) -> dict[str, int]:
            return {"y": self.x + 1}

    @task
    class Parent:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self, control: TaskControl) -> dict[str, int]:
            child = control.spawn(AddOne, x=self.x)
            child_execution = control.await_terminal(child)
            observed = control.read_execution(child.execution_id)
            control.publish("child_observed", {"status": child_execution.status})
            return {"y": observed.outputs["y"] * 2}

    store = SQLiteTraceStore()
    run = Parent.start(store=store, run_id="run:parent", x=10)  # type: ignore[attr-defined]
    execution = run.wait()
    history = project_effective_history_from_store(store, READER, run.cutoff)

    assert execution.outputs == {"y": 22}
    assert history.root.execution_id == run.execution_id
    assert [relation.relation_kind for relation in history.relations] == ["spawned"]
    assert len(history.children) == 1
    assert history.children[0].execution.parent_execution_id == run.execution_id
    assert history.children[0].execution.outputs == {"y": 11}
    assert history.published_facts[0].kind == "child_observed"
    assert history.published_facts[0].data == {"status": "succeeded"}

    child_terminal_fact_id = history.children[0].execution.terminal_fact_id
    assert child_terminal_fact_id is not None
    assert execution.terminal_fact_id is not None
    closure = store.read_causal_closure(TRUSTED_READ_CONTEXT, (execution.terminal_fact_id,))
    assert child_terminal_fact_id in closure.fact_ids()


def test_task_control_spawn_retains_failed_child_without_failing_parent() -> None:
    @task
    class Fails:
        def execute(self) -> None:
            raise RuntimeError("child boom")

    @task
    class Parent:
        def execute(self, control: TaskControl) -> dict[str, str]:
            child = control.spawn(Fails)
            child_execution = child.wait()
            return {"child_status": child_execution.status}

    store = SQLiteTraceStore()
    run = Parent.start(store=store, run_id="run:failed-child")  # type: ignore[attr-defined]
    history = project_effective_history_from_store(store, READER, run.cutoff)

    assert run.wait().status == "succeeded"
    assert run.wait().outputs == {"child_status": "failed"}
    assert len(history.children) == 1
    assert history.children[0].execution.status == "failed"
    assert history.children[0].execution.error == "RuntimeError: child boom"


def test_task_control_abandon_keeps_relation_but_removes_effective_child() -> None:
    @task
    class Child:
        def execute(self) -> dict[str, bool]:
            return {"done": True}

    @task
    class Parent:
        def execute(self, control: TaskControl) -> None:
            child = control.spawn(Child)
            control.abandon(child)

    store = SQLiteTraceStore()
    run = Parent.start(store=store, run_id="run:abandon-child")  # type: ignore[attr-defined]
    history = project_effective_history_from_store(store, READER, run.cutoff)

    assert [relation.relation_kind for relation in history.relations] == ["spawned", "abandoned"]
    assert history.children == ()


def test_task_control_adopt_records_existing_execution_as_effective_child() -> None:
    @task
    class Double:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self) -> dict[str, int]:
            return {"y": self.x * 2}

    @task
    class Parent:
        def __init__(self, child_execution_id: str, child_frontier_id: str) -> None:
            self.child_execution_id = child_execution_id
            self.child_frontier_id = child_frontier_id

        def execute(self, control: TaskControl) -> dict[str, int]:
            child = control.adopt(
                execution_id=self.child_execution_id,
                frontier_id=self.child_frontier_id,
            )
            return {"child_y": child.wait().outputs["y"]}

    store = SQLiteTraceStore()
    child = Double.start(store=store, run_id="run:external-child", x=7)  # type: ignore[attr-defined]
    run = Parent.start(  # type: ignore[attr-defined]
        store=store,
        run_id="run:adopt-parent",
        child_execution_id=child.execution_id,
        child_frontier_id=child.frontier_id,
    )
    history = project_effective_history_from_store(store, READER, run.cutoff)

    assert run.wait().outputs == {"child_y": 14}
    assert [relation.relation_kind for relation in history.relations] == ["adopted"]
    assert len(history.children) == 1
    assert history.children[0].execution.execution_id == child.execution_id
    assert history.children[0].execution.outputs == {"y": 14}


def test_child_history_can_be_projected_after_restart(tmp_path: Path) -> None:
    @task
    class Double:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self) -> dict[str, int]:
            return {"y": self.x * 2}

    @task
    class Parent:
        def __init__(self, x: int) -> None:
            self.x = x

        def execute(self, control: TaskControl) -> dict[str, int]:
            child = control.spawn(Double, x=self.x)
            return {"child_y": child.wait().outputs["y"]}

    db_path = tmp_path / "trace.sqlite"
    with SQLiteTraceStore(db_path) as store:
        run = Parent.start(store=store, run_id="run:restart-parent", x=21)  # type: ignore[attr-defined]
        frontier_id = run.frontier_id

    with SQLiteTraceStore(db_path) as restarted:
        history = project_effective_history_from_store(restarted, READER, restarted.read_owner_cutoff(frontier_id))

    assert history.root.outputs == {"child_y": 42}
    assert len(history.children) == 1
    assert history.children[0].execution.outputs == {"y": 42}
