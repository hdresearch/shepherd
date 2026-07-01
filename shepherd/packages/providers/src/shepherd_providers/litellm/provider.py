"""LiteLLMProvider — universal LLM provider via litellm.

Implements the Provider protocol from shepherd-core with its own multi-turn
agent loop. Supports 100+ models via a single model string.

Usage:
    provider = LiteLLMProvider(name="default", model="claude-sonnet-4-6")
    result = await provider.execute_sdk(prompt="Fix the bug", binding=None, runtime=runtime)
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any
from uuid import uuid4

from shepherd_core.effects import (
    AgentMessage,
    LLMResponseReceived,
    PromptSent,
    ToolCallCompleted,
    ToolCallStarted,
)
from shepherd_core.provider import Provider, ProviderRuntime
from shepherd_core.types import (
    ExecutionResult,
    ProviderBinding,
    ProviderCapabilities,
    ToolCall,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Tool schemas for each capability
_BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a shell command. Returns stdout, stderr, and exit code.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
            },
            "required": ["command"],
        },
    },
}

_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Read the contents of a file.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to read",
                },
            },
            "required": ["path"],
        },
    },
}

_WRITE_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write content to a file. Creates the file if it doesn't exist.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the file to write",
                },
                "content": {
                    "type": "string",
                    "description": "The content to write to the file",
                },
            },
            "required": ["path", "content"],
        },
    },
}

_CAPABILITY_TOOL_MAP = {
    "bash": [_BASH_TOOL],
    "read": [_READ_TOOL],
    "write": [_WRITE_TOOL],
}


class LiteLLMProvider(Provider):
    """Universal LLM provider via litellm.

    Implements the Provider protocol with its own multi-turn agent loop.
    Supports any model that litellm supports (100+ providers).
    """

    def __init__(
        self,
        name: str = "default",
        model: str = "claude-sonnet-4-6",
        max_turns: int = 30,
        **kwargs: Any,
    ):
        self.name = name
        self.model = model
        self.max_turns = max_turns
        self.kwargs = kwargs  # temperature, api_key, max_tokens, etc.
        self._device: Any = None  # ContainerDevice, set by Agent

    @property
    def provider_id(self) -> str:
        return f"litellm-{self.name}"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_type="litellm",
            supports_streaming=False,
            supports_tools=True,
            supports_structured_output=False,
            supports_session=False,
        )

    def set_device(self, device: Any) -> None:
        """Set the container device for tool execution."""
        self._device = device

    async def execute_sdk(
        self,
        prompt: str,
        binding: ProviderBinding | None,
        runtime: ProviderRuntime,
        hooks: dict | None = None,
    ) -> ExecutionResult:
        """Execute via litellm with multi-turn agent loop."""
        import litellm

        messages: list[dict[str, Any]] = []

        # Build system prompt from binding
        if binding and binding.system_prompt_additions:
            messages.append(
                {
                    "role": "system",
                    "content": "\n".join(binding.system_prompt_additions),
                }
            )

        messages.append({"role": "user", "content": prompt})

        # Build tool schemas from capabilities
        tools = self._build_tool_schemas(binding)

        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        last_content = ""

        # LLM response aggregation
        total_input_tokens = 0
        total_output_tokens = 0
        total_all_tokens = 0
        total_cost_usd = 0.0
        cost_available = False
        total_api_ms = 0.0
        last_model_id = self.model
        invocation_start = time.perf_counter()
        num_turns = 0

        for turn in range(self.max_turns):
            num_turns += 1
            runtime.effects.emit(
                PromptSent(
                    system_prompt="",
                    user_prompt=prompt if turn == 0 else "",
                    total_tokens=0,
                    model_id=self.model,
                )
            )

            completion_kwargs: dict[str, Any] = {
                "model": self.model,
                "messages": messages,
                **self.kwargs,
            }
            if tools:
                completion_kwargs["tools"] = tools

            api_start = time.perf_counter()
            response = await litellm.acompletion(**completion_kwargs)
            total_api_ms += (time.perf_counter() - api_start) * 1000

            message = response.choices[0].message
            last_content = message.content or ""

            # Aggregate token usage and cost
            usage = getattr(response, "usage", None)
            if usage:
                total_input_tokens += getattr(usage, "prompt_tokens", 0) or 0
                total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
                total_all_tokens += getattr(usage, "total_tokens", 0) or 0
            resp_model = getattr(response, "model", None)
            last_model_id = resp_model if isinstance(resp_model, str) else self.model
            try:
                turn_cost = litellm.completion_cost(completion_response=response)
                total_cost_usd += turn_cost
                cost_available = True
            except Exception:  # noqa: BLE001, S110
                pass  # Unknown model or pricing not available

            # Append assistant message to conversation
            msg_dict: dict[str, Any] = {"role": "assistant"}
            if message.content:
                msg_dict["content"] = message.content
            if message.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in message.tool_calls
                ]
            messages.append(msg_dict)

            # No tool calls — agent is done
            if not message.tool_calls:
                runtime.effects.emit(AgentMessage(content=last_content))
                break

            # Execute tool calls
            for tc in message.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                call_id = tc.id or str(uuid4())

                runtime.effects.emit(
                    ToolCallStarted(
                        tool_call_id=call_id,
                        tool_name=fn_name,
                        params=fn_args,
                    )
                )

                tool_call = ToolCall(id=call_id, name=fn_name, params=fn_args)

                tool_start = time.perf_counter()
                try:
                    result_str = self._dispatch_tool(fn_name, fn_args)
                    success = True
                except Exception as e:  # noqa: BLE001
                    result_str = f"Error: {e}"
                    success = False
                tool_duration_ms = (time.perf_counter() - tool_start) * 1000

                runtime.effects.emit(
                    ToolCallCompleted(
                        tool_call_id=call_id,
                        tool_name=fn_name,
                        success=success,
                        output=result_str[:2000],
                        duration_ms=tool_duration_ms,
                    )
                )

                all_tool_calls.append(tool_call)
                all_tool_results.append(
                    ToolResult(
                        tool_call_id=call_id,
                        success=success,
                        output=result_str,
                    )
                )

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": result_str,
                    }
                )

        runtime.effects.emit(
            LLMResponseReceived(
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
                total_tokens=total_all_tokens,
                cost_usd=total_cost_usd if cost_available else None,
                duration_ms=(time.perf_counter() - invocation_start) * 1000,
                duration_api_ms=total_api_ms,
                num_turns=num_turns,
                model_id=last_model_id,
                is_error=False,
            )
        )

        return ExecutionResult(
            success=True,
            output_text=last_content,
            tool_calls=tuple(all_tool_calls),
            tool_results=tuple(all_tool_results),
            metadata={"turns": turn + 1, "model": self.model},
        )

    def _build_tool_schemas(self, binding: ProviderBinding | None) -> list[dict[str, Any]]:
        """Build tool schemas from binding capabilities."""
        if binding is None:
            # Default: bash only
            return [_BASH_TOOL]

        tools: list[dict[str, Any]] = []
        for cap in binding.capabilities:
            if cap in _CAPABILITY_TOOL_MAP:
                tools.extend(_CAPABILITY_TOOL_MAP[cap])

        # Add custom tools from binding
        if binding.custom_tools:
            for tool_def in binding.custom_tools:
                tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool_def.name,
                            "description": tool_def.description or "",
                            "parameters": tool_def.input_schema or {"type": "object", "properties": {}},
                        },
                    }
                )

        return tools

    def _dispatch_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute a tool call and return the result as a string."""
        if name == "bash":
            return self._exec_bash(args.get("command", ""))
        if name == "read_file":
            return self._exec_read(args.get("path", ""))
        if name == "write_file":
            return self._exec_write(args.get("path", ""), args.get("content", ""))
        return f"Unknown tool: {name}"

    def _exec_bash(self, command: str) -> str:
        """Execute a bash command via device or local subprocess."""
        if self._device and hasattr(self._device, "exec_sync"):
            result = self._device.exec_sync(command)
            return f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\nexit_code: {result.return_code}"

        # Local fallback
        try:
            r = subprocess.run(
                ["bash", "-c", command],
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            parts = []
            if r.stdout:
                parts.append(f"stdout:\n{r.stdout}")
            if r.stderr:
                parts.append(f"stderr:\n{r.stderr}")
            parts.append(f"exit_code: {r.returncode}")
            return "\n".join(parts)
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 120s"

    def _exec_read(self, path: str) -> str:
        """Read a file."""
        if self._device and hasattr(self._device, "exec_sync"):
            result = self._device.exec_sync(f"cat {path}")
            return result.stdout if result.return_code == 0 else f"Error: {result.stderr}"
        try:
            with open(path) as f:
                return f.read()
        except Exception as e:  # noqa: BLE001
            return f"Error reading {path}: {e}"

    def _exec_write(self, path: str, content: str) -> str:
        """Write a file."""
        if self._device and hasattr(self._device, "exec_sync"):
            # Escape content for shell
            escaped = content.replace("'", "'\\''")
            result = self._device.exec_sync(f"cat > {path} << 'LITELLM_EOF'\n{escaped}\nLITELLM_EOF")
            return f"Written to {path}" if result.return_code == 0 else f"Error: {result.stderr}"
        try:
            with open(path, "w") as f:
                f.write(content)
            return f"Written to {path}"
        except Exception as e:  # noqa: BLE001
            return f"Error writing {path}: {e}"
