"""Tests for Tier 1: JSON export/import."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Literal

from shepherd_contexts.kvstore.effects import KeySet
from shepherd_core.effects import (
    KERNEL_EFFECT_REGISTRY,
    AgentMessage,
    Effect,
    PromptSent,
    TaskCompleted,
    TaskStarted,
    ToolCallCompleted,
    ToolCallStarted,
)
from shepherd_core.scope.stream import Stream
from shepherd_export.json_export import from_json, to_json
from shepherd_runtime.effects import compose_effect_registry


def _sample_stream() -> Stream:
    stream = Stream()
    stream = stream.append(TaskStarted(task_name="TestTask"))
    stream = stream.append(PromptSent(user_prompt="Do something"))
    stream = stream.append(AgentMessage(content="OK, doing it"))
    stream = stream.append(ToolCallStarted(tool_call_id="tc1", tool_name="bash", params={"command": "echo hi"}))
    stream = stream.append(ToolCallCompleted(tool_call_id="tc1", tool_name="bash", output="hi"))
    return stream.append(TaskCompleted(task_name="TestTask", duration_ms=100.0))


class TestToJson:
    def test_basic_export(self):
        stream = _sample_stream()
        result = to_json(stream)
        doc = json.loads(result)

        assert doc["total_effects"] == 6
        assert "task_started" in doc["effect_types"]
        assert len(doc["timeline"]) == 6

    def test_timeline_has_layer_metadata(self):
        stream = _sample_stream()
        doc = json.loads(to_json(stream))
        first = doc["timeline"][0]

        assert "_sequence" in first
        assert "_scope_id" in first
        assert "_scope_depth" in first
        assert first["effect_type"] == "task_started"

    def test_empty_stream(self):
        result = to_json(Stream())
        doc = json.loads(result)
        assert doc["total_effects"] == 0
        assert doc["timeline"] == []

    def test_effect_types_preserve_order(self):
        stream = _sample_stream()
        doc = json.loads(to_json(stream))
        assert doc["effect_types"][0] == "task_started"

    def test_write_to_file(self):
        stream = _sample_stream()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        to_json(stream, path=path)
        content = Path(path).read_text()
        doc = json.loads(content)
        assert doc["total_effects"] == 6

    def test_indent_option(self):
        stream = _sample_stream()
        compact = to_json(stream, indent=0)
        pretty = to_json(stream, indent=4)
        assert len(pretty) > len(compact)


class TestFromJson:
    def test_round_trip(self):
        original = _sample_stream()
        json_str = to_json(original)
        imported = from_json(json_str)

        assert len(imported) == len(original)
        for orig_layer, imp_layer in zip(original, imported, strict=False):
            assert orig_layer.effect.effect_type == imp_layer.effect.effect_type

    def test_from_file(self):
        stream = _sample_stream()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write(to_json(stream))
            path = f.name

        imported = from_json(path)
        assert len(imported) == len(stream)

    def test_from_file_path_object(self):
        stream = _sample_stream()
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            f.write(to_json(stream))
            path = Path(f.name)

        imported = from_json(path)
        assert len(imported) == len(stream)

    def test_from_raw_list(self):
        stream = _sample_stream()
        raw_list = json.dumps(stream.to_dicts(), default=str)
        imported = from_json(raw_list)
        assert len(imported) == len(stream)

    def test_empty_json(self):
        imported = from_json('{"total_effects": 0, "timeline": []}')
        assert len(imported) == 0

    def test_preserves_effect_types(self):
        original = _sample_stream()
        imported = from_json(to_json(original))

        original_types = [layer.effect.effect_type for layer in original]
        imported_types = [layer.effect.effect_type for layer in imported]
        assert original_types == imported_types

    def test_preserves_tool_call_fields(self):
        original = _sample_stream()
        imported = from_json(to_json(original))

        tc_layers = [layer for layer in imported if layer.effect.effect_type == "tool_call_started"]
        assert len(tc_layers) == 1
        assert tc_layers[0].effect.tool_name == "bash"
        assert tc_layers[0].effect.tool_call_id == "tc1"

    def test_registry_argument_controls_custom_effect_decode(self):
        class ExportOnlyEffect(Effect):
            effect_type: Literal["export_only_effect"] = "export_only_effect"
            payload: str = ""

        stream = Stream().append(ExportOnlyEffect(payload="x"))
        registry = KERNEL_EFFECT_REGISTRY.extend({"export_only_effect": ExportOnlyEffect})

        imported = from_json(to_json(stream), registry=registry)

        assert isinstance(imported[0].effect, ExportOnlyEffect)
        assert imported[0].effect.payload == "x"

    def test_kernel_default_decode_stays_fail_closed_for_contributor_effects(self):
        stream = Stream().append(KeySet(key="a", new_value="b"))

        imported = from_json(to_json(stream))

        assert type(imported[0].effect) is Effect
        assert imported[0].effect.effect_type == "key_set"

    def test_registry_argument_decodes_contributor_effects_with_runtime_composition(self):
        stream = Stream().append(KeySet(key="a", new_value="b"))

        imported = from_json(to_json(stream), registry=compose_effect_registry())

        assert isinstance(imported[0].effect, KeySet)
        assert imported[0].effect.key == "a"
