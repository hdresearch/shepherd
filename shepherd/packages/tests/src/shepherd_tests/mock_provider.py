"""Mock provider implementation for testing without external SDKs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shepherd_core.provider import Provider
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
)

if TYPE_CHECKING:
    from shepherd_core.provider import ProviderRuntime


def _is_taskref_source_schema(schema: dict[str, Any]) -> bool:
    """Detect the TaskRef provider contract from schema shape and description."""
    if schema.get("type") != "string":
        return False

    description = schema.get("description", "")
    return "Raw Python source for exactly one @task class" in description


def _is_taskref_source_array_schema(schema: dict[str, Any]) -> bool:
    """Detect list[TaskRef] schemas."""
    if schema.get("type") != "array":
        return False

    items = schema.get("items")
    return isinstance(items, dict) and _is_taskref_source_schema(items)


def _pascal_case_identifier(name: str) -> str:
    """Convert a schema/property name into a safe PascalCase identifier."""
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if not parts:
        return "MockGenerated"
    identifier = "".join(part[:1].upper() + part[1:] for part in parts)
    if identifier[0].isdigit():
        identifier = f"Mock{identifier}"
    return identifier


def _mock_task_source(field_name: str) -> str:
    """Generate valid stub source for TaskRef outputs in mock mode."""
    class_name = f"{_pascal_case_identifier(field_name)}Task"
    return "\n".join(
        [
            "@task",
            f"class {class_name}(BaseModel):",
            '    """Mock task generated for structured-output testing."""',
            "    text: Input(str)",
            "    result: Output(str)",
        ]
    )


@dataclass
class MockProvider(Provider):
    """A mock provider for testing without making real API calls."""

    name: str = "mock"
    mock: bool = True
    default_output: str = "[mock output]"
    structured_output: dict[str, Any] = field(default_factory=dict)
    mock_responses: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    _response_index: int = field(default=0, repr=False)

    @property
    def provider_id(self) -> str:
        """Return unique provider identifier."""
        return f"provider:mock:{self.name}"

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return mock capabilities."""
        return ProviderCapabilities(
            provider_type="mock",
            supports_streaming=False,
            supports_tools=True,
            supports_structured_output=True,
            supports_session=False,
            supports_fork_session=False,
            supports_images=False,
        )

    @property
    def formatter(self) -> Any:
        """Return None because MockProvider has no verbose formatter."""
        return None

    def validate_binding(self, binding: ProviderBinding) -> None:
        """Accept any binding."""

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        hooks: dict | None = None,
    ) -> ExecutionResult:
        """Mock execution that returns configured or generated values."""
        self.calls.append(
            {
                "prompt": prompt,
                "binding": binding,
                "hooks": hooks,
                "task_name": runtime.task_name,
            }
        )

        if self.mock_responses and self._response_index < len(self.mock_responses):
            response = self.mock_responses[self._response_index]
            self._response_index += 1
            return ExecutionResult(
                success=True,
                output_text=response.get("text", self.default_output),
                structured_output=response.get("structured", self.structured_output),
            )

        structured = dict(self.structured_output)
        if not structured and binding and binding.output_format:
            structured = self._generate_mock_from_output_format(binding.output_format)

        return ExecutionResult(
            success=True,
            output_text=self.default_output,
            structured_output=structured,
        )

    def _generate_mock_from_output_format(self, output_format: dict[str, Any]) -> dict[str, Any]:
        """Generate mock values from an output schema."""
        from shepherd_runtime.step.mock import generate_mock_value

        if output_format.get("type") == "json_schema" and "schema" in output_format:
            inner_schema = output_format["schema"]
            if inner_schema.get("type") == "object" and "properties" in inner_schema:
                result = {}
                task_sources: dict[str, str] = {}
                for prop_name, prop_schema in inner_schema["properties"].items():
                    if _is_taskref_source_schema(prop_schema):
                        source = _mock_task_source(prop_name)
                        task_sources[prop_name] = source
                        result[prop_name] = source
                        continue

                    if _is_taskref_source_array_schema(prop_schema):
                        result[prop_name] = [_mock_task_source(prop_name)]  # type: ignore[assignment]
                        continue

                    source_field_prefix = prop_name.removesuffix("_source")
                    if (
                        prop_name.endswith("_source")
                        and source_field_prefix in task_sources
                        and prop_schema.get("type") == "string"
                    ):
                        result[prop_name] = task_sources[source_field_prefix]
                        continue

                    result[prop_name] = generate_mock_value(prop_schema, prop_name)
                return result

        return {"result": generate_mock_value(output_format, "result")}

    def reset(self) -> None:
        """Reset call tracking and the queued response index."""
        self.calls.clear()
        self._response_index = 0


__all__ = [
    "MockProvider",
]
