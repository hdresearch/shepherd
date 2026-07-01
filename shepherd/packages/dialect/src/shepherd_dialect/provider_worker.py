"""Parser for the confined SDK/API provider worker ABI."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from shepherd_dialect.provider_capabilities import canonical_tool_payload
from shepherd_dialect.provider_runtime import (
    ProviderEvent,
    ProviderInvocationResult,
    redacted_text_payload,
)

WORKER_SCHEMA_VERSION = "shepherd.provider_worker.v1"
RECORD_PROVIDER_EVENT = "provider_event"
RECORD_PROVIDER_RESULT = "provider_result"

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|secret|password|authorization|bearer|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|sk-ant-[A-Za-z0-9_-]{8,})\b")


class ProviderWorkerProtocolError(ValueError):
    """Base class for malformed provider worker output."""


class ProviderWorkerJSONError(ProviderWorkerProtocolError):
    """Raised when stdout contains non-JSON protocol lines."""


class ProviderWorkerSchemaError(ProviderWorkerProtocolError):
    """Raised when a worker record has an unsupported schema version."""


class ProviderWorkerRecordError(ProviderWorkerProtocolError):
    """Raised when worker record cardinality or type is invalid."""


class ProviderWorkerSequenceError(ProviderWorkerProtocolError):
    """Raised when worker-provided sequence values are not monotonic."""


class ProviderWorkerSecretError(ProviderWorkerProtocolError):
    """Raised when worker records contain raw secret-like material."""


@dataclass(frozen=True)
class ParsedWorkerOutput:
    """Normalized output parsed from worker stdout."""

    result: ProviderInvocationResult
    events: tuple[ProviderEvent, ...]
    diagnostics: Mapping[str, object] = field(default_factory=dict)


def parse_provider_worker_output(
    stdout: str | bytes,
    *,
    provider_id: str,
    invocation_id: str,
    model: str | None = None,
    sequence_start: int = 0,
    stderr: str | bytes | None = None,
) -> ParsedWorkerOutput:
    """Parse `shepherd.provider_worker.v1` NDJSON stdout into provider runtime objects."""
    text = _decode(stdout)
    worker_events: list[ProviderEvent] = []
    result: ProviderInvocationResult | None = None
    last_worker_sequence: int | None = None

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            raw_record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProviderWorkerJSONError(f"worker stdout line {line_no} is not JSON") from exc
        if not isinstance(raw_record, Mapping):
            raise ProviderWorkerRecordError(f"worker stdout line {line_no} is not an object")
        record = dict(raw_record)
        _reject_secret_like(record, path=f"line[{line_no}]")

        schema_version = record.get("schema_version")
        if schema_version != WORKER_SCHEMA_VERSION:
            raise ProviderWorkerSchemaError(
                f"worker stdout line {line_no} has unsupported schema_version: {schema_version!r}"
            )
        record_type = record.get("record_type")
        if result is not None:
            if record_type == RECORD_PROVIDER_RESULT:
                raise ProviderWorkerRecordError("worker emitted duplicate provider_result")
            raise ProviderWorkerRecordError("worker emitted records after provider_result")

        worker_sequence = record.get("sequence")
        if worker_sequence is not None:
            if not isinstance(worker_sequence, int) or worker_sequence < 0:
                raise ProviderWorkerSequenceError("worker sequence must be a non-negative integer")
            if last_worker_sequence is not None and worker_sequence <= last_worker_sequence:
                raise ProviderWorkerSequenceError("worker sequence values must be strictly monotonic")
            last_worker_sequence = worker_sequence

        if record_type == RECORD_PROVIDER_EVENT:
            worker_events.append(
                _provider_event_from_worker_record(
                    record,
                    provider_id=provider_id,
                    invocation_id=invocation_id,
                    model=model,
                    sequence=sequence_start + len(worker_events),
                )
            )
        elif record_type == RECORD_PROVIDER_RESULT:
            result = _provider_result_from_worker_record(record)
        else:
            raise ProviderWorkerRecordError(f"unknown worker record_type: {record_type!r}")

    if result is None:
        raise ProviderWorkerRecordError("worker stdout did not include provider_result")

    parsed_result = ProviderInvocationResult(
        output_text=result.output_text,
        structured_output=result.structured_output,
        session_id=result.session_id,
        usage=result.usage,
        events=tuple(worker_events),
        metadata=result.metadata,
    )
    return ParsedWorkerOutput(
        result=parsed_result,
        events=tuple(worker_events),
        diagnostics={
            **redacted_text_payload(text, field="stdout"),
            **redacted_text_payload(stderr, field="stderr"),
        },
    )


def _provider_event_from_worker_record(
    record: Mapping[str, Any],
    *,
    provider_id: str,
    invocation_id: str,
    model: str | None,
    sequence: int,
) -> ProviderEvent:
    _verify_identity(record, provider_id=provider_id, invocation_id=invocation_id)
    payload = record.get("payload", {})
    if not isinstance(payload, Mapping):
        raise ProviderWorkerRecordError("provider_event payload must be an object")
    payload = _canonicalized_payload(payload)
    caused_by = record.get("caused_by_event_ids", ())
    if caused_by is None:
        caused_by = ()
    if not isinstance(caused_by, list | tuple) or not all(isinstance(item, str) for item in caused_by):
        raise ProviderWorkerRecordError("caused_by_event_ids must be a list of strings")
    event_id = record.get("event_id")
    if event_id is None:
        event_id = f"{invocation_id}:worker:{sequence}"
    if not isinstance(event_id, str) or not event_id:
        raise ProviderWorkerRecordError("provider_event event_id must be a non-empty string")
    event_model = record.get("model", model)
    if event_model is not None and not isinstance(event_model, str):
        raise ProviderWorkerRecordError("provider_event model must be a string when present")
    tool_call_id = record.get("tool_call_id")
    if tool_call_id is not None and not isinstance(tool_call_id, str):
        raise ProviderWorkerRecordError("provider_event tool_call_id must be a string when present")
    kind = record.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ProviderWorkerRecordError("provider_event kind must be a non-empty string")
    return ProviderEvent(
        kind=kind,
        provider_id=provider_id,
        invocation_id=invocation_id,
        sequence=sequence,
        event_id=event_id,
        model=event_model,
        tool_call_id=tool_call_id,
        caused_by_event_ids=tuple(caused_by),
        payload=payload,
    )


def _provider_result_from_worker_record(record: Mapping[str, Any]) -> ProviderInvocationResult:
    output_text = record.get("output_text", "")
    if not isinstance(output_text, str):
        raise ProviderWorkerRecordError("provider_result output_text must be a string")
    structured_output = record.get("structured_output", {})
    if structured_output is None:
        structured_output = {}
    if not isinstance(structured_output, Mapping):
        raise ProviderWorkerRecordError("provider_result structured_output must be an object")
    session_id = record.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise ProviderWorkerRecordError("provider_result session_id must be a string when present")
    usage = record.get("usage", {})
    if usage is None:
        usage = {}
    if not isinstance(usage, Mapping):
        raise ProviderWorkerRecordError("provider_result usage must be an object")
    metadata = record.get("metadata", {})
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, Mapping):
        raise ProviderWorkerRecordError("provider_result metadata must be an object")
    return ProviderInvocationResult(
        output_text=output_text,
        structured_output=dict(structured_output),
        session_id=session_id,
        usage=dict(usage),
        metadata=dict(metadata),
    )


def _verify_identity(record: Mapping[str, Any], *, provider_id: str, invocation_id: str) -> None:
    raw_provider_id = record.get("provider_id")
    if raw_provider_id is not None and raw_provider_id != provider_id:
        raise ProviderWorkerRecordError("worker provider_id does not match parent provider")
    raw_invocation_id = record.get("invocation_id")
    if raw_invocation_id is not None and raw_invocation_id != invocation_id:
        raise ProviderWorkerRecordError("worker invocation_id does not match parent invocation")


def _canonicalized_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    value = dict(payload)
    tool_name = value.get("tool_name")
    if isinstance(tool_name, str) and "canonical_tool_name" not in value:
        value.update(canonical_tool_payload(tool_name))
    return value


def _reject_secret_like(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}"
            if _SECRET_KEY_RE.search(key_text):
                raise ProviderWorkerSecretError(f"worker record contains secret-like field {child_path}")
            _reject_secret_like(child, path=child_path)
    elif isinstance(value, list | tuple):
        for index, child in enumerate(value):
            _reject_secret_like(child, path=f"{path}[{index}]")
    elif isinstance(value, str) and _SECRET_VALUE_RE.search(value):
        raise ProviderWorkerSecretError(f"worker record contains secret-like value at {path}")


def _decode(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


__all__ = [
    "RECORD_PROVIDER_EVENT",
    "RECORD_PROVIDER_RESULT",
    "WORKER_SCHEMA_VERSION",
    "ParsedWorkerOutput",
    "ProviderWorkerJSONError",
    "ProviderWorkerProtocolError",
    "ProviderWorkerRecordError",
    "ProviderWorkerSchemaError",
    "ProviderWorkerSecretError",
    "ProviderWorkerSequenceError",
    "parse_provider_worker_output",
]
