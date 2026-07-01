"""Runtime-normalized trace recorder tests for Phase 1 evidence."""

from __future__ import annotations

import json

import pytest
from shepherd_kernel_v3_reference.trace.validate import validate_runtime_trace
from shepherd_runtime.trace import (
    SURFACE_REGISTRY,
    ArtifactEmitted,
    DeliveryCompleted,
    EffectDeclaration,
    EffectRequested,
    HandlerReturned,
    HandlerSelected,
    ProviderCallCompleted,
    ProviderCallRequested,
    RunRef,
    RuntimeSurfaceEvent,
    RuntimeTraceRecorder,
    RuntimeTraceRecorderError,
    SubstrateRefused,
    SubTag,
    Trace,
    active_trace_recorder,
    pop_trace_recorder,
    push_trace_recorder,
)


class _LeakyObject:
    def __repr__(self) -> str:
        return "LeakyObject(secret=repr-secret)"


def test_runtime_surface_event_registers_and_round_trips() -> None:
    assert SURFACE_REGISTRY["RuntimeSurfaceEvent"] is RuntimeSurfaceEvent
    assert SURFACE_REGISTRY["EffectRequested"] is EffectRequested
    assert SURFACE_REGISTRY["HandlerSelected"] is HandlerSelected
    assert SURFACE_REGISTRY["HandlerReturned"] is HandlerReturned
    assert SURFACE_REGISTRY["ProviderCallRequested"] is ProviderCallRequested
    assert SURFACE_REGISTRY["ProviderCallCompleted"] is ProviderCallCompleted
    assert SURFACE_REGISTRY["DeliveryCompleted"] is DeliveryCompleted
    assert SURFACE_REGISTRY["ArtifactEmitted"] is ArtifactEmitted
    assert SURFACE_REGISTRY["SubstrateRefused"] is SubstrateRefused

    run_ref = RunRef(id="run-surface")
    event = RuntimeSurfaceEvent(
        ref="surface:1",
        timestamp_us=1,
        run_ref=run_ref,
        sequence=1,
        family="effect",
        phase="requested",
        kind="effect_requested",
        status="requested",
        effect_key="ask.PickOne",
        payload={"option_count": 2},
    )
    trace = Trace(run_ref=run_ref, surface=(event,))

    assert Trace.from_json(trace.to_json()) == trace


def test_typed_runtime_surface_event_round_trips() -> None:
    run_ref = RunRef(id="run-typed-surface")
    event = EffectRequested(
        ref="surface:effect-requested",
        timestamp_us=1,
        run_ref=run_ref,
        sequence=1,
        status="requested",
        effect_key="ask.PickOne",
        payload={"payload_summary": {"option_count": 2}},
    )
    trace = Trace(run_ref=run_ref, surface=(event,))

    assert Trace.from_json(trace.to_json()) == trace


@pytest.mark.parametrize(
    "event_type",
    [
        RuntimeSurfaceEvent,
        EffectRequested,
        HandlerSelected,
        HandlerReturned,
        ProviderCallRequested,
        ProviderCallCompleted,
        DeliveryCompleted,
        ArtifactEmitted,
        SubstrateRefused,
    ],
)
def test_registered_runtime_surface_event_type_round_trips(event_type: type[RuntimeSurfaceEvent]) -> None:
    run_ref = RunRef(id=f"run-{event_type.__name__}")
    event = event_type(
        ref=f"surface:{event_type.__name__}",
        timestamp_us=1,
        run_ref=run_ref,
        sequence=1,
        status="recorded",
        effect_key="model.call",
        handler_key="handler.test.v1",
        payload={"summary": {"count": 1}},
    )
    trace = Trace(run_ref=run_ref, surface=(event,))

    assert SURFACE_REGISTRY[event_type.__name__] is event_type
    assert Trace.from_json(trace.to_json()) == trace


def test_recorder_records_handled_ask_as_runtime_valid_trace() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-ask"))

    declaration = recorder.record_effect_requested(
        "ask.PickOne",
        payload_summary={"option_count": 2, "prompt": "choose the best"},
    )
    selection = recorder.record_handler_selected(
        declaration,
        handler_key="local.pick_one.v1",
    )
    recorder.record_effect_completed(
        selection,
        result_summary={"result_shape": "str"},
    )
    trace = recorder.to_trace()

    validate_runtime_trace(trace.kernel)
    assert Trace.from_json(trace.to_json()) == trace
    assert isinstance(trace.surface[0], EffectRequested)
    assert isinstance(trace.surface[1], HandlerSelected)
    assert isinstance(trace.surface[2], HandlerReturned)
    assert [event.sequence for event in trace.surface] == [1, 2, 3]
    assert [event.kind for event in trace.surface] == [
        "effect_requested",
        "handler_selected",
        "handler_returned",
    ]
    assert trace.surface[0].payload["payload_summary"]["prompt"] == "<redacted>"


def test_recorder_records_default_ignored_tell() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-tell"))

    declaration = recorder.record_effect_requested(
        "tell.Audit",
        payload_summary={"severity": "info"},
    )
    recorder.record_effect_default_ignored(declaration)
    trace = recorder.to_trace()

    validate_runtime_trace(trace.kernel)
    assert Trace.from_json(trace.to_json()) == trace
    assert trace.surface[1].handler_key == "runtime.default_ignore.v1"
    assert trace.surface[1].status == "default_ignored"
    assert trace.surface[2].status == "default_ignored"
    assert trace.surface[2].payload["result_summary"] == {"ignored": True}


def test_recorder_records_model_call_and_delivery_completion() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-provider"))

    declaration = recorder.record_provider_call_requested(
        request_summary={
            "message_count": 1,
            "tool_count": 0,
            "model": "fake-model",
            "prompt": "raw prompt must not appear",
        },
    )
    selection = recorder.record_handler_selected(
        declaration,
        handler_key="bypass.v1",
    )
    capture = recorder.record_provider_call_completed(
        selection,
        response_summary={
            "response_shape": "structured_output",
            "finish_reason": "end_turn",
            "content": {"result": "raw response must not appear"},
        },
    )
    recorder.record_delivery_completed(
        result_type="Summary",
        status="completed",
        citing=(capture,),
        detail_summary={"coercion": "ok"},
    )
    trace = recorder.to_trace()

    validate_runtime_trace(trace.kernel)
    assert Trace.from_json(trace.to_json()) == trace
    assert isinstance(trace.surface[0], ProviderCallRequested)
    assert isinstance(trace.surface[1], HandlerSelected)
    assert isinstance(trace.surface[2], ProviderCallCompleted)
    assert isinstance(trace.surface[3], DeliveryCompleted)
    declarations = [record for record in trace.kernel if isinstance(record, EffectDeclaration)]
    assert [record.effect_kind for record in declarations] == ["model.call"]
    assert [event.family for event in trace.surface] == [
        "provider",
        "handler",
        "provider",
        "delivery",
    ]
    assert trace.surface[0].payload["request_summary"]["prompt"] == "<redacted>"
    assert trace.surface[2].payload["response_summary"]["content"] == "<redacted>"
    assert trace.surface[3].sub_tag is SubTag.run
    assert trace.surface[3].payload["detail_summary"] == {"coercion": "ok"}


def test_recorder_records_typed_artifact_surface_event() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-artifact"))

    recorder.record_artifact_emitted(
        artifact_kind="json",
        name="summary",
        metadata_summary={"field_count": 3},
    )
    trace = recorder.to_trace()

    assert Trace.from_json(trace.to_json()) == trace
    assert isinstance(trace.surface[0], ArtifactEmitted)
    assert trace.surface[0].sub_tag is SubTag.artifact
    assert trace.surface[0].payload["artifact_kind"] == "json"


def test_recorder_records_substrate_refusal_surface_event() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-refused"))

    ref = recorder.record_substrate_refused(
        source="vcs-core",
        profile="ReadOnly",
        driver_id="shepherd.workspace_ref",
        reason="observation evidence_kind denied by active surface",
        offending="python-runtime:write",
        operation="external write via builtins.open",
        path="blocked.txt",
    )
    trace = recorder.to_trace()

    assert Trace.from_json(trace.to_json()) == trace
    assert trace.cite(ref) is trace.surface[0]
    assert isinstance(trace.surface[0], SubstrateRefused)
    assert trace.surface[0].sub_tag is SubTag.run
    assert trace.surface[0].payload["source"] == "vcs-core"
    assert trace.surface[0].payload["profile"] == "ReadOnly"
    assert trace.surface[0].payload["offending"] == "python-runtime:write"


def test_recorder_payload_sanitizer_redacts_sensitive_key_variants() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-redaction"))
    long_note = "x" * 600

    recorder.record_provider_call_requested(
        request_summary={
            "access_token": "access-token-secret",
            "apiKey": "api-key-secret",
            "authorizationHeader": "bearer secret",
            "content_length": 123,
            "message_count": 2,
            "messages": ["raw message secret"],
            "nested": [{"secret": "nested secret", "safe": "ok"}],
            "note": long_note,
            "prompt_text": "raw prompt secret",
            "rawPrompt": "raw camel prompt secret",
            "raw_response": "raw response secret",
            "response_body": "body secret",
            "response_shape": "text",
            "response_summary": {"finish_reason": "end_turn"},
            "unknown": _LeakyObject(),
        }
    )
    trace = recorder.to_trace()
    payload = trace.surface[0].payload["request_summary"]

    assert payload["access_token"] == "<redacted>"
    assert payload["apiKey"] == "<redacted>"
    assert payload["authorizationHeader"] == "<redacted>"
    assert payload["messages"] == "<redacted>"
    assert payload["nested"] == [{"secret": "<redacted>", "safe": "ok"}]
    assert payload["prompt_text"] == "<redacted>"
    assert payload["rawPrompt"] == "<redacted>"
    assert payload["raw_response"] == "<redacted>"
    assert payload["response_body"] == "<redacted>"
    assert payload["content_length"] == 123
    assert payload["message_count"] == 2
    assert payload["response_shape"] == "text"
    assert payload["response_summary"] == {"finish_reason": "end_turn"}
    assert payload["note"] == {"type": "str", "length": 600, "redacted": True}
    assert payload["unknown"] == {"type": "_LeakyObject", "redacted": True}

    serialized = json.dumps(trace.to_json())
    assert "access-token-secret" not in serialized
    assert "api-key-secret" not in serialized
    assert "raw prompt secret" not in serialized
    assert "raw response secret" not in serialized
    assert "nested secret" not in serialized
    assert "repr-secret" not in serialized
    assert long_note not in serialized


def test_recorder_rejects_reserved_claim_payload_keys() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-claim-guard"))

    with pytest.raises(RuntimeTraceRecorderError, match=r"payload_summary\.claim_level"):
        recorder.record_effect_requested(
            "ask.PickOne",
            payload_summary={"claim_level": "proof-backed"},
        )

    with pytest.raises(RuntimeTraceRecorderError, match=r"request_summary\.nested\.0\.proof_profile"):
        recorder.record_provider_call_requested(
            request_summary={"nested": [{"proof_profile": "lean"}]},
        )

    with pytest.raises(RuntimeTraceRecorderError, match=r"result_type\.claim_level"):
        recorder.record_delivery_completed(
            result_type={"claim_level": "proof-backed"},  # type: ignore[arg-type]
            status="completed",
        )

    with pytest.raises(RuntimeTraceRecorderError, match=r"detail_summary\.proof_profile"):
        recorder.record_delivery_completed(
            result_type="Summary",
            status="completed",
            detail_summary={"proof_profile": "lean"},
        )


def test_recorder_preserves_kernel_record_order() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-order"))

    declaration = recorder.record_effect_requested("ask.PickOne")
    selection = recorder.record_handler_selected(declaration, handler_key="local.pick_one.v1")
    recorder.record_effect_completed(selection)
    trace = recorder.to_trace()

    assert [type(record).__name__ for record in trace.kernel] == [
        "EffectDeclaration",
        "HandlerSelection",
        "ResumptionHandle",
        "EffectCapture",
    ]


def test_recorder_rejects_duplicate_completion() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-duplicate"))

    declaration = recorder.record_effect_requested("ask.PickOne")
    selection = recorder.record_handler_selected(declaration, handler_key="local.pick_one.v1")
    recorder.record_effect_completed(selection)

    with pytest.raises(RuntimeTraceRecorderError, match="already completed"):
        recorder.record_effect_completed(selection)


def test_recorder_rejects_duplicate_handler_selection() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-duplicate-selection"))

    declaration = recorder.record_effect_requested("ask.PickOne")
    recorder.record_handler_selected(declaration, handler_key="local.pick_one.v1")

    with pytest.raises(RuntimeTraceRecorderError, match="already has a selected handler"):
        recorder.record_handler_selected(declaration, handler_key="local.pick_two.v1")


def test_recorder_rejects_unknown_declaration_and_selection_refs() -> None:
    recorder = RuntimeTraceRecorder(RunRef(id="run-unknown"))

    with pytest.raises(RuntimeTraceRecorderError, match="unknown effect declaration ref"):
        recorder.record_handler_selected("declaration:missing", handler_key="local.pick_one.v1")

    with pytest.raises(RuntimeTraceRecorderError, match="unknown handler selection ref"):
        recorder.record_effect_completed("selection:missing")

    with pytest.raises(RuntimeTraceRecorderError, match="unknown handler selection ref"):
        recorder.record_provider_call_completed("selection:missing")


def test_active_trace_recorder_context_stack() -> None:
    outer = RuntimeTraceRecorder(RunRef(id="run-outer"))
    inner = RuntimeTraceRecorder(RunRef(id="run-inner"))

    assert active_trace_recorder() is None
    outer_token = push_trace_recorder(outer)
    try:
        assert active_trace_recorder() is outer
        inner_token = push_trace_recorder(inner)
        try:
            assert active_trace_recorder() is inner
        finally:
            pop_trace_recorder(inner_token)
        assert active_trace_recorder() is outer
    finally:
        pop_trace_recorder(outer_token)
    assert active_trace_recorder() is None
