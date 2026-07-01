"""shepherd-trace-viewer: local viewer for durable Shepherd task traces."""

from __future__ import annotations

from shepherd_trace_viewer.durable_reader import (
    DurableTraceReadError,
    read_trace_payload,
    read_trace_payload_file,
    read_trace_revision,
)
from shepherd_trace_viewer.embed import render_static_html, write_static_html
from shepherd_trace_viewer.model import (
    SCHEMA_VERSION,
    TraceEdge,
    TraceLane,
    TraceNode,
    TraceResource,
    TraceRun,
    TraceSource,
    TraceView,
)
from shepherd_trace_viewer.serde import SchemaVersionError, from_json, to_json
from shepherd_trace_viewer.trace_store_reader import (
    TraceStoreReadError,
    read_trace_store_session_view,
    read_trace_store_view,
)

__all__ = [
    "SCHEMA_VERSION",
    "DurableTraceReadError",
    "SchemaVersionError",
    "TraceEdge",
    "TraceLane",
    "TraceNode",
    "TraceResource",
    "TraceRun",
    "TraceSource",
    "TraceStoreReadError",
    "TraceView",
    "from_json",
    "read_trace_payload",
    "read_trace_payload_file",
    "read_trace_revision",
    "read_trace_store_session_view",
    "read_trace_store_view",
    "render_static_html",
    "to_json",
    "write_static_html",
]
