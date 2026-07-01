from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest
from shepherd_kernel_v3_reference.trace.validate import validate_runtime_trace
from shepherd_runtime.effects import (
    Ask,
    HandlerSignatureError,
    Tell,
    UnhandledAsk,
    UnhandledTell,
    ask,
    handle,
    sync_ask,
    sync_tell,
    tell,
)
from shepherd_runtime.trace import RunRef, Trace
from shepherd_runtime.trace.runtime import RuntimeTraceRecorder, pop_trace_recorder, push_trace_recorder


@dataclass(frozen=True)
class _Pick(Ask[str], kind="runtime_dispatch.pick"):
    options: tuple[str, ...]


@dataclass(frozen=True)
class _Audit(Tell, kind="runtime_dispatch.audit"):
    message: str


def test_ask_dispatches_to_nearest_typed_handler() -> None:
    async def run() -> str:
        async with handle(_Pick, lambda effect: "outer"), handle(_Pick, lambda effect: effect.options[0]):
            return await ask(_Pick(options=("inner", "fallback")))

    assert asyncio.run(run()) == "inner"


def test_sync_handle_context_dispatches_inside_async_task() -> None:
    async def run() -> str:
        with handle(_Pick, lambda effect: effect.options[0]):
            await asyncio.sleep(0)
            return await ask(_Pick(options=("sync-with", "fallback")))

    assert asyncio.run(run()) == "sync-with"


def test_ask_handler_stack_is_restored_after_context() -> None:
    async def run() -> None:
        async with handle(_Pick, lambda effect: effect.options[0]):
            assert await ask(_Pick(options=("ok",))) == "ok"
        with pytest.raises(UnhandledAsk):
            await ask(_Pick(options=("missing",)))

    asyncio.run(run())


def test_ask_handler_stack_is_isolated_across_concurrent_async_tasks() -> None:
    async def run_one(value: str) -> str:
        async with handle(_Pick, lambda effect: value):
            await asyncio.sleep(0)
            return await ask(_Pick(options=("unused",)))

    async def run() -> tuple[str, str]:
        left, right = await asyncio.gather(run_one("left"), run_one("right"))
        with pytest.raises(UnhandledAsk):
            await ask(_Pick(options=("missing",)))
        return left, right

    assert asyncio.run(run()) == ("left", "right")


def test_child_task_created_inside_handle_inherits_context_snapshot() -> None:
    async def run() -> str:
        async def child() -> str:
            await asyncio.sleep(0)
            return await ask(_Pick(options=("unused",)))

        async with handle(_Pick, lambda effect: "snapshot"):
            task = asyncio.create_task(child())

        with pytest.raises(UnhandledAsk):
            await ask(_Pick(options=("parent-restored",)))
        return await task

    assert asyncio.run(run()) == "snapshot"


def test_tell_dispatches_when_handler_exists_and_ignores_return_value() -> None:
    seen: list[str] = []

    async def run() -> None:
        async with handle(_Audit, lambda effect: seen.append(effect.message) or "ignored"):
            result = await tell(_Audit(message="recorded"))
            assert result is None

    asyncio.run(run())
    assert seen == ["recorded"]


def test_tell_preserves_default_ignore_when_unhandled() -> None:
    async def run() -> None:
        assert await tell(_Audit(message="ignored")) is None

    asyncio.run(run())


def test_tell_raise_policy_still_raises_when_unhandled() -> None:
    @dataclass(frozen=True)
    class Critical(Tell, on_unhandled="raise"):
        message: str

    async def run() -> None:
        with pytest.raises(UnhandledTell):
            await tell(Critical(message="fail"))

    asyncio.run(run())


def test_sync_ask_dispatches_to_nearest_typed_handler() -> None:
    with handle(_Pick, lambda effect: effect.options[0]):
        assert sync_ask(_Pick(options=("sync", "fallback"))) == "sync"


def test_sync_tell_dispatches_when_handler_exists() -> None:
    seen: list[str] = []

    with handle(_Audit, lambda effect: seen.append(effect.message)):
        assert sync_tell(_Audit(message="sync-recorded")) is None

    assert seen == ["sync-recorded"]


def test_sync_tell_preserves_default_ignore_when_unhandled() -> None:
    assert sync_tell(_Audit(message="ignored")) is None


def test_sync_dispatch_preserves_handler_context_inside_running_loop() -> None:
    async def run() -> str:
        with handle(_Pick, lambda effect: effect.options[0]):
            await asyncio.sleep(0)
            return sync_ask(_Pick(options=("thread-hop", "fallback")))

    assert asyncio.run(run()) == "thread-hop"


def test_string_handler_matches_exact_key_only() -> None:
    from shepherd_runtime.effects._handler_stack import resolve_handler

    with handle("model.call", lambda request: request):
        assert resolve_handler("model.call") is not None
        assert resolve_handler("model.other") is None
        assert resolve_handler(_Pick(options=("x",))) is None


def test_string_handler_matches_exact_effect_kind() -> None:
    from shepherd_runtime.effects._handler_stack import resolve_handler

    with handle("runtime_dispatch.pick", lambda effect: effect.options[0]):
        assert resolve_handler(_Pick(options=("x",))) is not None
        assert resolve_handler(_Audit(message="x")) is None


def test_class_handler_and_parent_subtree_match_child_kind() -> None:
    @dataclass(frozen=True)
    class Parent(Tell, kind="runtime_dispatch.parent"):
        label: str

    @dataclass(frozen=True)
    class Child(Parent, kind="runtime_dispatch.parent.child"):
        pass

    from shepherd_runtime.effects._handler_stack import resolve_handler

    event = Child(label="x")

    with handle(Parent, lambda effect: effect.label):
        assert resolve_handler(event) is not None


def test_wildcard_handler_key_rejects_with_deferred_message() -> None:
    with pytest.raises(HandlerSignatureError, match=r"wildcard.*deferred"):
        handle("runtime_dispatch.**", lambda event: event)


def test_handle_rejects_keyword_arguments() -> None:
    with pytest.raises(HandlerSignatureError, match="keyword arguments"):
        handle(_Pick, lambda effect: effect.options[0], unexpected=True)


def test_handle_rejects_required_keyword_only_handler_parameter() -> None:
    def invalid(effect: _Pick, *, required: str) -> str:
        del effect
        return required

    with pytest.raises(HandlerSignatureError, match="required keyword-only"):
        handle(_Pick, invalid)


def test_handled_ask_records_runtime_valid_trace() -> None:
    async def run() -> Trace:
        recorder = RuntimeTraceRecorder(RunRef(id="run-effect-ask"))
        token = push_trace_recorder(recorder)
        try:
            async with handle(_Pick, lambda effect: effect.options[0]):
                assert await ask(_Pick(options=("alpha", "beta"))) == "alpha"
            return recorder.to_trace()
        finally:
            pop_trace_recorder(token)

    trace = asyncio.run(run())

    validate_runtime_trace(trace.kernel)
    assert Trace.from_json(trace.to_json()) == trace
    assert [event.kind for event in trace.surface] == [
        "effect_requested",
        "handler_selected",
        "handler_returned",
    ]
    assert trace.surface[0].effect_key == "runtime_dispatch.pick"
    assert trace.surface[2].payload["result_summary"] == {"result_type": "str"}


def test_sync_handled_ask_records_runtime_valid_trace() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-effect-sync-ask"))
    token = push_trace_recorder(recorder)
    try:
        with handle(_Pick, lambda effect: effect.options[0]):
            assert sync_ask(_Pick(options=("alpha", "beta"))) == "alpha"
        trace = recorder.to_trace()
    finally:
        pop_trace_recorder(token)

    validate_runtime_trace(trace.kernel)
    assert Trace.from_json(trace.to_json()) == trace
    assert [event.kind for event in trace.surface] == [
        "effect_requested",
        "handler_selected",
        "handler_returned",
    ]
    assert trace.surface[0].effect_key == "runtime_dispatch.pick"


def test_default_ignored_tell_records_runtime_valid_trace() -> None:
    async def run() -> Trace:
        recorder = RuntimeTraceRecorder(RunRef(id="run-effect-tell"))
        token = push_trace_recorder(recorder)
        try:
            await tell(_Audit(message="ignored"))
            return recorder.to_trace()
        finally:
            pop_trace_recorder(token)

    trace = asyncio.run(run())

    validate_runtime_trace(trace.kernel)
    assert Trace.from_json(trace.to_json()) == trace
    assert [event.kind for event in trace.surface] == [
        "effect_requested",
        "handler_selected",
        "handler_returned",
    ]
    assert trace.surface[0].effect_key == "runtime_dispatch.audit"
    assert trace.surface[1].handler_key == "runtime.default_ignore.v1"
    assert trace.surface[1].status == "default_ignored"
    assert trace.surface[2].status == "default_ignored"


def test_unhandled_ask_and_raising_tell_do_not_leave_incomplete_kernel_declarations() -> None:
    @dataclass(frozen=True)
    class Critical(Tell, on_unhandled="raise"):
        message: str

    async def run() -> Trace:
        recorder = RuntimeTraceRecorder(RunRef(id="run-unhandled"))
        token = push_trace_recorder(recorder)
        try:
            with pytest.raises(UnhandledAsk):
                await ask(_Pick(options=("missing",)))
            with pytest.raises(UnhandledTell):
                await tell(Critical(message="missing"))
            return recorder.to_trace()
        finally:
            pop_trace_recorder(token)

    trace = asyncio.run(run())

    assert trace.kernel == ()
    assert trace.surface == ()
