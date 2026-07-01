from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest
from shepherd_core.schema import SINGLE_OUTPUT_KEY
from shepherd_kernel_v3_reference.trace.validate import validate_runtime_trace
from shepherd_runtime.effects import Resumption, handle
from shepherd_runtime.nucleus import Failed, Stopped, deliver, reset_workspace_for_tests, task, workspace
from shepherd_runtime.provider_boundary import ModelRequest, ModelResponse


@pytest.fixture(autouse=True)
def reset_workspace() -> None:
    reset_workspace_for_tests()
    yield
    reset_workspace_for_tests()


@dataclass(frozen=True)
class _FakeModel:
    name: str = "handled-model"
    provider_id: str = "provider.fake"


def _assert_model_call_lifecycle(run, *, completion_status: str, delivery_status: str) -> dict[str, object]:
    assert run.trace is not None
    request_event, selection_event, completion_event, delivery_event = run.trace.surface[-4:]
    detail = delivery_event.payload["detail_summary"]
    lifecycle = detail["provider_call_lifecycle"]

    assert request_event.kind == "provider_call_requested"
    assert selection_event.kind == "handler_selected"
    assert completion_event.kind == "provider_call_completed"
    assert delivery_event.kind == "delivery_completed"
    assert lifecycle == {
        "request_ref": request_event.citing[0],
        "request_status": "requested",
        "selection_ref": selection_event.citing[1],
        "selection_status": "selected",
        "completion_ref": completion_event.citing[1],
        "completion_status": completion_status,
        "delivery_status": delivery_status,
    }
    assert delivery_event.citing == (completion_event.citing[1],)
    return detail


@pytest.mark.asyncio
async def test_deliver_uses_handled_model_call_request_response_path(tmp_path) -> None:
    calls: list[ModelRequest] = []

    async def fake_model(request: ModelRequest) -> ModelResponse:
        calls.append(request)
        return ModelResponse(structured_output={SINGLE_OUTPUT_KEY: "ok"})

    workspace(model=_FakeModel(), root=tmp_path)

    @task
    async def label(topic: str) -> str:
        return await deliver(str, goal="label the topic", evidence=[topic])

    async with handle("model.call", fake_model):
        run = await label.detailed("phase1")

    assert run.unwrap() == "ok"
    assert calls
    assert calls[0].settings.model == "handled-model"
    assert calls[0].tools == ()
    assert calls[0].messages[0].role == "user"
    assert "phase1" in calls[0].messages[0].content
    assert run.trace is not None
    assert run.trace.kernel
    assert run.trace.surface
    validate_runtime_trace(run.trace.kernel)
    assert type(run.trace).from_json(run.trace.to_json()) == run.trace
    assert [event.kind for event in run.trace.surface[-3:]] == [
        "handler_selected",
        "provider_call_completed",
        "delivery_completed",
    ]
    request_summary = run.trace.surface[-4].payload["request_summary"]
    response_summary = run.trace.surface[-2].payload["response_summary"]
    assert request_summary["provider_id"] == "provider.fake"
    assert request_summary["model_id"] == "handled-model"
    assert request_summary["status"] == "requested"
    assert response_summary["provider_id"] == "provider.fake"
    assert response_summary["model_id"] == "handled-model"
    assert response_summary["status"] == "returned"
    assert response_summary["tool_call_count"] == 0
    assert run.trace.surface[-1].status == "completed"
    assert {event.claim_level for event in run.trace.surface} == {"phase1-runtime"}
    assert {event.proof_profile for event in run.trace.surface} == {"runtime_only"}

    detail = _assert_model_call_lifecycle(run, completion_status="returned", delivery_status="completed")
    assert detail["reason"] == "structured_output_coerced"
    assert detail["result_type"] == "str"
    assert detail["model_id"] == "handled-model"
    assert detail["provider_id"] == "provider.fake"
    assert detail["handler_id"].startswith("local.model.call:")
    assert detail["response_shape"] == "structured_output"
    assert detail["structured_key_count"] == 1


@pytest.mark.asyncio
async def test_deliver_requires_handled_model_call_on_nucleus_path(tmp_path) -> None:
    workspace(model="fake", root=tmp_path)

    @task
    async def label() -> str:
        return await deliver(str, goal="label the topic")

    run = await label.detailed()

    assert isinstance(run.outcome, Failed)
    assert run.outcome.error_type == "UnhandledModelCall"
    assert "deliver(str)" in run.outcome.message
    assert "no handler is installed" in run.outcome.message
    assert 'handle("model.call"' in run.outcome.message
    assert run.trace is not None
    assert run.trace.kernel == ()
    assert [event.kind for event in run.trace.surface] == ["delivery_completed"]
    assert run.trace.surface[0].status == "failed"
    detail = run.trace.surface[0].payload["detail_summary"]
    assert detail["reason"] == "no_model_call_handler"
    assert detail["model_id"] == "fake"
    assert 'handle("model.call"' in detail["next_step"]


@pytest.mark.asyncio
async def test_model_call_rejects_supervisor_handler_shape(tmp_path) -> None:
    workspace(model="fake", root=tmp_path)

    async def supervisor(
        request: ModelRequest,
        resume: Resumption[ModelRequest, ModelResponse],
    ) -> ModelResponse:
        return await resume(request)

    @task
    async def label() -> str:
        return await deliver(str, goal="label the topic")

    async with handle("model.call", supervisor):
        run = await label.detailed()

    assert isinstance(run.outcome, Failed)
    assert run.outcome.error_type == "HandlerSignatureError"
    assert "model.call handler" in run.outcome.message
    assert "supervisor handlers are not supported" in run.outcome.message
    assert run.trace is not None
    validate_runtime_trace(run.trace.kernel)
    assert [event.kind for event in run.trace.surface] == [
        "provider_call_requested",
        "handler_selected",
        "provider_call_completed",
        "delivery_completed",
    ]
    assert run.trace.surface[-2].status == "raised"
    assert run.trace.surface[-1].status == "failed"
    detail = run.trace.surface[-1].payload["detail_summary"]
    assert detail["reason"] == "handler_failed"
    assert detail["exception_type"] == "HandlerSignatureError"
    assert detail["error_type"] == "HandlerSignatureError"
    assert detail["result_type"] == "str"
    assert detail["handler_id"].startswith("local.model.call:")
    assert _assert_model_call_lifecycle(run, completion_status="raised", delivery_status="failed") == detail


@pytest.mark.asyncio
async def test_model_call_rejects_non_model_response_handler_result(tmp_path) -> None:
    workspace(model="fake", root=tmp_path)

    def malformed_model(request: ModelRequest) -> dict[str, object]:
        assert request.settings.model == "fake"
        return {SINGLE_OUTPUT_KEY: "ok"}

    @task
    async def label() -> str:
        return await deliver(str, goal="label the topic")

    async with handle("model.call", malformed_model):
        run = await label.detailed()

    assert isinstance(run.outcome, Failed)
    assert run.outcome.error_type == "InvalidModelResponse"
    assert "expected ModelResponse, got dict" in run.outcome.message
    assert "structured_output" in run.outcome.message
    assert run.trace is not None
    validate_runtime_trace(run.trace.kernel)
    assert [event.kind for event in run.trace.surface] == [
        "provider_call_requested",
        "handler_selected",
        "provider_call_completed",
        "delivery_completed",
    ]
    assert run.trace.surface[-2].status == "raised"
    assert run.trace.surface[-1].status == "failed"
    detail = run.trace.surface[-1].payload["detail_summary"]
    assert detail["reason"] == "handler_returned_invalid_response"
    assert detail["error_type"] == "InvalidModelResponse"
    assert detail["handler_id"].startswith("local.model.call:")
    assert detail["handler_result_type"] == "dict"
    failure_summary = run.trace.surface[-2].payload["response_summary"]
    assert failure_summary["reason"] == "handler_returned_invalid_response"
    assert failure_summary["delivery_error_type"] == "InvalidModelResponse"
    assert _assert_model_call_lifecycle(run, completion_status="raised", delivery_status="failed") == detail


@pytest.mark.asyncio
async def test_model_call_text_response_reports_structured_output_requirement(tmp_path) -> None:
    workspace(model="fake", root=tmp_path)

    def text_model(request: ModelRequest) -> ModelResponse:
        assert request.settings.model == "fake"
        return ModelResponse(text="plain text")

    @task
    async def label() -> str:
        return await deliver(str, goal="label the topic")

    async with handle("model.call", text_model):
        run = await label.detailed()

    assert isinstance(run.outcome, Failed)
    assert run.outcome.error_type == "MissingStructuredOutput"
    assert "received a text ModelResponse" in run.outcome.message
    assert "typed delivery requires structured_output" in run.outcome.message
    assert run.trace is not None
    validate_runtime_trace(run.trace.kernel)
    assert run.trace.surface[-2].status == "returned"
    assert run.trace.surface[-1].status == "failed"
    detail = run.trace.surface[-1].payload["detail_summary"]
    assert detail["reason"] == "response_without_structured_output"
    assert detail["response_shape"] == "text"
    assert detail["structured_key_count"] == 0
    assert detail["result_type"] == "str"
    assert detail["model_id"] == "fake"
    assert _assert_model_call_lifecycle(run, completion_status="returned", delivery_status="failed") == detail


@pytest.mark.asyncio
async def test_structured_output_coercion_failure_diagnostics_omit_payload_values(tmp_path) -> None:
    workspace(model=_FakeModel(), root=tmp_path)
    secret_value = "secret structured output token"

    def malformed_model(request: ModelRequest) -> ModelResponse:
        assert request.settings.model == "handled-model"
        return ModelResponse(structured_output={SINGLE_OUTPUT_KEY: secret_value})

    @task
    async def classify() -> int:
        return await deliver(int, goal="classify the topic")

    async with handle("model.call", malformed_model):
        run = await classify.detailed()

    assert isinstance(run.outcome, Failed)
    assert run.outcome.error_type in {"TypeError", "ValueError"}
    assert "could not coerce model.call structured_output into int" in run.outcome.message
    assert secret_value not in run.outcome.message
    assert run.trace is not None
    validate_runtime_trace(run.trace.kernel)
    assert [event.kind for event in run.trace.surface] == [
        "provider_call_requested",
        "handler_selected",
        "provider_call_completed",
        "delivery_completed",
    ]
    assert run.trace.surface[-2].status == "returned"
    assert run.trace.surface[-1].status == "failed"

    response_summary = run.trace.surface[-2].payload["response_summary"]
    assert response_summary["status"] == "returned"
    assert response_summary["response_shape"] == "structured_output"
    assert response_summary["structured_keys"] == [SINGLE_OUTPUT_KEY]

    detail = run.trace.surface[-1].payload["detail_summary"]
    assert detail["reason"] == "structured_output_coercion_failed"
    assert detail["response_shape"] == "structured_output"
    assert detail["structured_key_count"] == 1
    assert detail["exception_type"] == run.outcome.error_type
    assert detail["error_type"] == run.outcome.error_type
    assert detail["result_type"] == "int"
    assert detail["model_id"] == "handled-model"
    assert _assert_model_call_lifecycle(run, completion_status="returned", delivery_status="failed") == detail

    serialized_trace = json.dumps(run.trace.to_json())
    assert secret_value not in serialized_trace
    assert secret_value not in repr(response_summary)
    assert secret_value not in repr(detail)


@pytest.mark.asyncio
async def test_model_call_handler_error_closes_request_failure_evidence(tmp_path) -> None:
    workspace(model=_FakeModel(), root=tmp_path)

    def failing_model(request: ModelRequest) -> ModelResponse:
        assert request.settings.model == "handled-model"
        raise RuntimeError("raw failure message")

    @task
    async def label(topic: str) -> str:
        return await deliver(str, goal="label the topic", evidence=[topic])

    async with handle("model.call", failing_model):
        run = await label.detailed("phase1")

    assert isinstance(run.outcome, Failed)
    assert run.outcome.error_type == "RuntimeError"
    assert "model.call handler" in run.outcome.message
    assert "raw failure message" in run.outcome.message
    assert run.trace is not None
    validate_runtime_trace(run.trace.kernel)
    assert [event.kind for event in run.trace.surface] == [
        "provider_call_requested",
        "handler_selected",
        "provider_call_completed",
        "delivery_completed",
    ]
    request_summary = run.trace.surface[0].payload["request_summary"]
    failure_summary = run.trace.surface[2].payload["response_summary"]
    assert request_summary["provider_id"] == "provider.fake"
    assert failure_summary["provider_id"] == "provider.fake"
    assert failure_summary["model_id"] == "handled-model"
    assert failure_summary["status"] == "raised"
    assert failure_summary["failure_class"] == "RuntimeError"
    assert failure_summary["reason"] == "handler_failed"
    assert failure_summary["delivery_error_type"] == "RuntimeError"
    assert failure_summary["tool_call_count"] == 0
    assert "raw failure message" not in repr(failure_summary)
    assert run.trace.surface[2].status == "raised"
    assert run.trace.surface[3].status == "failed"
    detail = run.trace.surface[3].payload["detail_summary"]
    assert detail["reason"] == "handler_failed"
    assert detail["exception_type"] == "RuntimeError"
    assert detail["error_type"] == "RuntimeError"
    assert detail["result_type"] == "str"
    assert detail["model_id"] == "handled-model"
    assert _assert_model_call_lifecycle(run, completion_status="raised", delivery_status="failed") == detail
    assert "raw failure message" not in repr(detail)


@pytest.mark.asyncio
async def test_model_call_handler_cancellation_closes_stopped_evidence(tmp_path) -> None:
    workspace(model=_FakeModel(), root=tmp_path)

    async def cancelling_model(request: ModelRequest) -> ModelResponse:
        assert request.settings.model == "handled-model"
        raise asyncio.CancelledError("raw cancellation message")

    @task
    async def label(topic: str) -> str:
        return await deliver(str, goal="label the topic", evidence=[topic])

    async with handle("model.call", cancelling_model):
        run = await label.detailed("phase1")

    assert isinstance(run.outcome, Stopped)
    assert "model.call handler" in run.outcome.reason
    assert "was cancelled" in run.outcome.reason
    assert "raw cancellation message" not in run.outcome.reason
    assert run.trace is not None
    validate_runtime_trace(run.trace.kernel)
    assert [event.kind for event in run.trace.surface] == [
        "provider_call_requested",
        "handler_selected",
        "provider_call_completed",
        "delivery_completed",
    ]
    request_summary = run.trace.surface[0].payload["request_summary"]
    cancellation_summary = run.trace.surface[2].payload["response_summary"]
    assert request_summary["provider_id"] == "provider.fake"
    assert cancellation_summary["provider_id"] == "provider.fake"
    assert cancellation_summary["model_id"] == "handled-model"
    assert cancellation_summary["status"] == "cancelled"
    assert cancellation_summary["failure_class"] == "CancelledError"
    assert cancellation_summary["reason"] == "handler_cancelled"
    assert cancellation_summary["delivery_error_type"] == "CancelledError"
    assert run.trace.surface[2].status == "cancelled"
    assert run.trace.surface[3].status == "stopped"
    detail = run.trace.surface[3].payload["detail_summary"]
    assert detail["reason"] == "handler_cancelled"
    assert detail["exception_type"] == "CancelledError"
    assert detail["error_type"] == "CancelledError"
    assert detail["result_type"] == "str"
    assert detail["model_id"] == "handled-model"
    assert _assert_model_call_lifecycle(run, completion_status="cancelled", delivery_status="stopped") == detail
    assert "raw cancellation message" not in repr(cancellation_summary)
    assert "raw cancellation message" not in repr(detail)


@pytest.mark.asyncio
async def test_external_task_cancellation_propagates_through_model_call(tmp_path) -> None:
    workspace(model=_FakeModel(), root=tmp_path)
    handler_started = asyncio.Event()

    async def slow_model(request: ModelRequest) -> ModelResponse:
        assert request.settings.model == "handled-model"
        handler_started.set()
        await asyncio.sleep(60)
        return ModelResponse(structured_output={SINGLE_OUTPUT_KEY: "ok"})

    @task
    async def label(topic: str) -> str:
        return await deliver(str, goal="label the topic", evidence=[topic])

    async with handle("model.call", slow_model):
        task_run = asyncio.create_task(label.detailed("phase1"))
        await handler_started.wait()
        task_run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task_run
