"""Immutable effect stream with query support.

This module defines:
- EffectLayer: Wrapper around Effect with metadata (sequence, source_context)
- Stream: Immutable, append-only sequence of effects
- Rich query API for filtering by attribution and type
- Serialization support

Core Philosophy: "There is only the effect stream"
- All state changes are recorded as effects
- Effects are immutable and ordered
- Rich querying enables filtering and analysis
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, overload

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from shepherd_core.effects import Effect, EffectTypeRegistry, TaskFailed
    from shepherd_core.effects.views import (
        CausalityTreeView,
        CostsView,
        IntentsView,
        OutcomesView,
        ProfileView,
        ThinkingView,
    )

E = TypeVar("E", bound="Effect")


@dataclass(frozen=True)
class EffectLayer:
    """Wrapper around an Effect with stream metadata.

    Attributes:
        effect: The wrapped effect instance
        sequence: Position in the stream (0-indexed)
        source_context: Denormalized context_id for fast filtering
        scope_id: ID of the scope that emitted this effect (for direct() filtering)
        scope_depth: Depth in scope hierarchy (for by_depth() filtering)
    """

    effect: Effect
    sequence: int
    source_context: str | None = None
    scope_id: str | None = None
    scope_depth: int = 0

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the wrapped effect for convenience."""
        return getattr(self.effect, name)


@dataclass(frozen=True)
class TimelineEntry:
    """A single entry in a timeline view (D45).

    Provides a relative timestamp from the start of the stream,
    useful for understanding temporal ordering and debugging.

    Attributes:
        relative_seconds: Seconds since the first effect in the stream.
        effect: The effect instance.
        layer: The full EffectLayer with metadata.
    """

    relative_seconds: float
    effect: Effect
    layer: EffectLayer

    def __str__(self) -> str:
        return f"{self.relative_seconds:07.3f}s  {type(self.effect).__name__}"


@dataclass(frozen=True)
class Stream:
    """Immutable, append-only sequence of effects.

    Usage:
        stream = Stream()
        stream = stream.append(TaskStarted(...))
        stream = stream.append(ToolCallStarted(...))

        # Access layers with metadata
        for layer in stream.layers:
            print(layer.effect, layer.sequence)

        # Query by type
        for layer in stream.query(TaskStarted):
            print(layer.effect.task_name)

        # Query by attribution
        for layer in stream.query(provider_id="provider:claude:..."):
            print(layer.effect)

        # Combine filters
        for layer in stream.query(ToolCallCompleted, task_name="my_task"):
            print(layer.effect.tool_name)

        # Slice by attribution
        task_stream = stream.by_task("my_task")

        # Tasks-as-scopes queries (requires scope-bound stream)
        direct_effects = scope.effects.direct()  # Only this scope's effects
        boundaries = scope.effects.summarized()  # Task boundaries only
        shallow = scope.effects.by_depth(2)  # Up to 2 levels deep
    """

    _layers: tuple[EffectLayer, ...] = ()
    _scope_id: str | None = None  # Optional scope context for direct()/by_depth()
    _scope_depth: int = 0  # Cached depth for by_depth() filtering

    # --- Properties ---

    @property
    def layers(self) -> tuple[EffectLayer, ...]:
        """Access the effect layers with metadata."""
        return self._layers

    # --- Mutation (returns new Stream) ---

    def append(self, effect: Effect) -> Stream:
        """Return new stream with effect appended.

        Note: This creates a layer without scope metadata. For scope-aware
        emission, use append_layer() with a pre-constructed EffectLayer.
        """
        sequence = len(self._layers)
        source_context = getattr(effect, "context_id", None)
        layer = EffectLayer(effect=effect, sequence=sequence, source_context=source_context)
        return Stream(_layers=(*self._layers, layer), _scope_id=self._scope_id, _scope_depth=self._scope_depth)

    def append_layer(self, layer: EffectLayer) -> Stream:
        """Return new stream with pre-constructed layer appended.

        The layer's scope_id and scope_depth are preserved. Only the
        sequence number is updated to match this stream's position.
        """
        updated_layer = EffectLayer(
            effect=layer.effect,
            sequence=len(self._layers),
            source_context=layer.source_context,
            scope_id=layer.scope_id,
            scope_depth=layer.scope_depth,
        )
        return Stream(_layers=(*self._layers, updated_layer), _scope_id=self._scope_id, _scope_depth=self._scope_depth)

    def extend(self, effects: tuple[Effect, ...] | list[Effect]) -> Stream:
        """Return new stream with multiple effects appended."""
        if isinstance(effects, list):
            effects = tuple(effects)
        new_layers = list(self._layers)
        for effect in effects:
            sequence = len(new_layers)
            source_context = getattr(effect, "context_id", None)
            layer = EffectLayer(effect=effect, sequence=sequence, source_context=source_context)
            new_layers.append(layer)
        return Stream(_layers=tuple(new_layers), _scope_id=self._scope_id, _scope_depth=self._scope_depth)

    def truncate_to(self, position: int) -> Stream:
        """Return new stream with only effects up to position (exclusive).

        Used by checkpoint/restore to discard effects after a savepoint.

        Args:
            position: Number of effects to keep (0 to position-1)

        Returns:
            New stream with truncated layers, preserving scope context.
            If position >= current length, returns self unchanged.

        Raises:
            ValueError: If position is negative

        Example:
            stream = Stream().append(e0).append(e1).append(e2)
            truncated = stream.truncate_to(2)  # keeps e0, e1
            assert len(truncated) == 2
        """
        if position < 0:
            raise ValueError(f"position must be >= 0, got {position}")
        if position >= len(self._layers):
            return self  # Nothing to truncate

        return Stream(
            _layers=self._layers[:position],
            _scope_id=self._scope_id,
            _scope_depth=self._scope_depth,
        )

    # --- Iteration ---

    def __iter__(self) -> Iterator[EffectLayer]:
        """Iterate over effect layers."""
        return iter(self._layers)

    def __len__(self) -> int:
        return len(self._layers)

    def __getitem__(self, index: int) -> EffectLayer:
        return self._layers[index]

    def __bool__(self) -> bool:
        return len(self._layers) > 0

    # --- Querying ---

    @overload
    def query(self, effect_type: type[E]) -> Iterator[EffectLayer]: ...

    @overload
    def query(
        self,
        effect_type: type[E],
        *,
        task_name: str | None = None,
        provider_id: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> Iterator[EffectLayer]: ...

    @overload
    def query(
        self,
        effect_type: None = None,
        *,
        task_name: str | None = None,
        provider_id: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> Iterator[EffectLayer]: ...

    def query(
        self,
        effect_type: type[Effect] | None = None,
        *,
        task_name: str | None = None,
        provider_id: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> Iterator[EffectLayer]:
        """Query effects by type and/or attribution.

        Args:
            effect_type: Filter to only this effect type (or subclass)
            task_name: Filter to effects from this task
            provider_id: Filter to effects from this provider
            context_id: Filter to effects related to this context (semantic routing)
            binding_name: Filter to effects for this binding (stable routing)

        Yields:
            EffectLayers matching all specified criteria
        """
        for layer in self._layers:
            effect = layer.effect
            if effect_type is not None and not isinstance(effect, effect_type):
                continue
            if task_name is not None and effect.task_name != task_name:
                continue
            if provider_id is not None and effect.provider_id != provider_id:
                continue
            if context_id is not None and getattr(effect, "context_id", None) != context_id:
                continue
            if binding_name is not None and getattr(effect, "binding_name", None) != binding_name:
                continue
            yield layer

    @overload
    def first(
        self,
        effect_type: type[E],
        *,
        task_name: str | None = ...,
        provider_id: str | None = ...,
        context_id: str | None = ...,
        binding_name: str | None = ...,
    ) -> EffectLayer | None: ...

    @overload
    def first(
        self,
        effect_type: None = ...,
        *,
        task_name: str | None = ...,
        provider_id: str | None = ...,
        context_id: str | None = ...,
        binding_name: str | None = ...,
    ) -> EffectLayer | None: ...

    def first(
        self,
        effect_type: type[Effect] | None = None,
        *,
        task_name: str | None = None,
        provider_id: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> EffectLayer | None:
        """Return first matching effect layer, or None.

        Use layer.effect to access the underlying effect.
        """
        for layer in self.query(
            effect_type, task_name=task_name, provider_id=provider_id, context_id=context_id, binding_name=binding_name
        ):
            return layer
        return None

    @overload
    def last(
        self,
        effect_type: type[E],
        *,
        task_name: str | None = ...,
        provider_id: str | None = ...,
        context_id: str | None = ...,
        binding_name: str | None = ...,
    ) -> EffectLayer | None: ...

    @overload
    def last(
        self,
        effect_type: None = ...,
        *,
        task_name: str | None = ...,
        provider_id: str | None = ...,
        context_id: str | None = ...,
        binding_name: str | None = ...,
    ) -> EffectLayer | None: ...

    def last(
        self,
        effect_type: type[Effect] | None = None,
        *,
        task_name: str | None = None,
        provider_id: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> EffectLayer | None:
        """Return last matching effect layer, or None.

        Use layer.effect to access the underlying effect.
        """
        result: EffectLayer | None = None
        for layer in self.query(
            effect_type, task_name=task_name, provider_id=provider_id, context_id=context_id, binding_name=binding_name
        ):
            result = layer
        return result

    def count(
        self,
        effect_type: type[Effect] | None = None,
        *,
        task_name: str | None = None,
        provider_id: str | None = None,
        context_id: str | None = None,
        binding_name: str | None = None,
    ) -> int:
        """Count matching effects."""
        return sum(
            1
            for _ in self.query(
                effect_type,
                task_name=task_name,
                provider_id=provider_id,
                context_id=context_id,
                binding_name=binding_name,
            )
        )

    # --- Slicing by Attribution ---

    def by_task(self, task_name: str) -> Stream:
        """Return new stream with only effects from given task."""
        return Stream(
            _layers=tuple(self.query(task_name=task_name)), _scope_id=self._scope_id, _scope_depth=self._scope_depth
        )

    def by_context(self, context_id: str) -> Stream:
        """Return new stream with only effects related to given context (semantic routing)."""
        return Stream(
            _layers=tuple(self.query(context_id=context_id)), _scope_id=self._scope_id, _scope_depth=self._scope_depth
        )

    def by_binding(self, binding_name: str) -> Stream:
        """Return new stream with only effects for given binding (stable routing)."""
        return Stream(
            _layers=tuple(self.query(binding_name=binding_name)),
            _scope_id=self._scope_id,
            _scope_depth=self._scope_depth,
        )

    def by_context_type(self, prefix: str) -> Stream:
        """Return new stream with only effects whose context_id starts with prefix.

        Example:
            stream.by_context_type("workspace:")  # All workspace effects
            stream.by_context_type("session:")    # All session effects
        """
        filtered = [
            layer
            for layer in self._layers
            if layer.source_context is not None and layer.source_context.startswith(prefix)
        ]
        return Stream(_layers=tuple(filtered), _scope_id=self._scope_id, _scope_depth=self._scope_depth)

    def by_provider(self, provider_id: str) -> Stream:
        """Return new stream with only effects from given provider."""
        return Stream(
            _layers=tuple(self.query(provider_id=provider_id)), _scope_id=self._scope_id, _scope_depth=self._scope_depth
        )

    # --- Scope-Aware Queries (Tasks-as-Scopes) ---

    def with_scope_context(self, scope_id: str, scope_depth: int = 0) -> Stream:
        """Return stream with scope context for direct()/by_depth() filtering.

        This is called automatically by ScopeProxy.effects property.

        Args:
            scope_id: The scope's unique identifier
            scope_depth: The scope's depth in the hierarchy (for by_depth)
        """
        return Stream(_layers=self._layers, _scope_id=scope_id, _scope_depth=scope_depth)

    def direct(self) -> Stream:
        """Effects emitted directly to this scope (excludes propagated from children).

        Use this for "what did THIS scope do" vs "what happened in this subtree".

        Example:
            with Scope() as parent:
                result = MyTask(input="test")  # Creates child scope

                # All effects (including from child task)
                all_effects = parent.effects

                # Only effects emitted directly by parent (excludes task effects)
                direct = parent.effects.direct()

        Raises:
            ValueError: If called on a stream without scope context
        """
        if self._scope_id is None:
            raise ValueError("direct() requires a scope-bound stream. Use scope.effects.direct() not Stream().direct()")
        filtered = tuple(layer for layer in self._layers if layer.scope_id == self._scope_id)
        return Stream(_layers=filtered, _scope_id=self._scope_id, _scope_depth=self._scope_depth)

    def summarized(self) -> Stream:
        """Only task boundaries: TaskStarted, TaskCompleted, TaskFailed.

        The "public interface" of tasks - what happened, not how.
        Useful for high-level understanding without implementation details.

        Note: Unlike direct() and by_depth(), this method does NOT require
        a scope-bound stream. It filters purely by effect type, so it works
        on any stream including Stream.from_json() results.

        Example:
            # See what tasks ran without tool call details
            for layer in scope.effects.summarized():
                print(layer.effect)  # TaskStarted or TaskCompleted
        """
        from shepherd_core.effects import TaskCompleted, TaskFailed, TaskStarted

        filtered = tuple(
            layer for layer in self._layers if isinstance(layer.effect, (TaskStarted, TaskCompleted, TaskFailed))
        )
        return Stream(_layers=filtered, _scope_id=self._scope_id, _scope_depth=self._scope_depth)

    def by_depth(self, max_depth: int) -> Stream:
        """Effects up to N levels deep in the scope hierarchy.

        Args:
            max_depth: Maximum depth relative to this scope.
                depth=0: only this scope's direct effects
                depth=1: this scope + immediate child tasks
                depth=N: this scope + N levels of nested tasks

        Example:
            # See this scope's effects plus one level of child tasks
            shallow = scope.effects.by_depth(1)

        Raises:
            ValueError: If called on a stream without scope context
            ValueError: If max_depth is negative
        """
        if self._scope_id is None:
            raise ValueError(
                "by_depth() requires a scope-bound stream. Use scope.effects.by_depth() not Stream().by_depth()"
            )
        if max_depth < 0:
            raise ValueError(f"max_depth must be >= 0, got {max_depth}")

        max_allowed = self._scope_depth + max_depth
        filtered = tuple(layer for layer in self._layers if layer.scope_depth <= max_allowed)
        return Stream(_layers=filtered, _scope_id=self._scope_id, _scope_depth=self._scope_depth)

    # --- Causality Lookup ---

    def get(self, sequence: int) -> Effect | None:
        """Look up an effect by its sequence number.

        Enables causality chain traversal via the `caused_by` field on effects.

        Args:
            sequence: The sequence number of the effect to find.

        Returns:
            The Effect if found, None otherwise.

        Example:
            for patch in result.effects.query(WorkspacePatchCaptured):
                intent = result.effects.get(patch.caused_by)
                if intent:
                    print(f"{patch.path} was caused by {intent.tool_name}")
        """
        for layer in self._layers:
            if layer.sequence == sequence:
                return layer.effect
        return None

    # --- Analysis ---

    def tasks(self) -> set[str]:
        """Get all unique task names in the stream."""
        return {layer.effect.task_name for layer in self._layers if layer.effect.task_name is not None}

    def contexts(self) -> set[str]:
        """Get all unique context IDs in the stream."""
        return {layer.source_context for layer in self._layers if layer.source_context is not None}

    def providers(self) -> set[str]:
        """Get all unique provider IDs in the stream."""
        return {layer.effect.provider_id for layer in self._layers if layer.effect.provider_id is not None}

    def effect_types(self) -> set[str]:
        """Get all unique effect types in the stream."""
        return {layer.effect.effect_type for layer in self._layers}

    # --- Debug Views ---

    def debug_summary(
        self,
        max_effects_per_task: int = 20,
        show_nested: bool = True,
        max_depth: int | None = None,
    ) -> str:
        """Detailed timeline of effects grouped by task.

        Shows execution flow with sequence numbers, nested scope indentation,
        and error details for failed tasks. Designed for debugging SDK errors.

        Args:
            max_effects_per_task: Maximum effects to show per task (default 20).
                Additional effects are summarized as "... N more effects".
            show_nested: If True, show indentation for nested scope depth.
            max_depth: If set, only show effects up to this depth.

        Returns:
            Multi-line debug summary string.

        Example output:
            Effect Stream Debug Summary
            ========================================
            Total effects: 20
            Tasks: 2 (1 completed, 1 failed)

            Task: FindImprovements [completed in 65.2ms] OK
              #  0 TaskStarted                              depth=0
              #  1 ContextPrepared (WorkspaceRef)           depth=0
              ...

            Task: ImplementFix [failed] FAILED
              # 15 TaskStarted                              depth=0
              # 17   TaskStarted (subtask)                  depth=1
              # 20 TaskFailed FAILED                        depth=0
                   Error: Command failed with exit code 1
                   Location: provider.py:830 in execute_sdk
                   Suggestions:
                     - Try again in a fresh owner-path Scope
        """
        from shepherd_core.effects import TaskCompleted, TaskFailed

        lines = []
        lines.append("Effect Stream Debug Summary")
        lines.append("=" * 40)
        lines.append(f"Total effects: {len(self)}")

        # Count task statuses
        task_names = list(self.tasks())
        completed_count = 0
        failed_count = 0
        for task_name in task_names:
            if self.first(TaskCompleted, task_name=task_name):
                completed_count += 1
            elif self.first(TaskFailed, task_name=task_name):
                failed_count += 1

        lines.append(f"Tasks: {len(task_names)} ({completed_count} completed, {failed_count} failed)")
        lines.append("")

        # Group effects by task
        for task_name in task_names:
            task_layers = list(self.query(task_name=task_name))
            if max_depth is not None:
                task_layers = [layer for layer in task_layers if layer.scope_depth <= max_depth]

            # Determine task status
            completed_layer = self.first(TaskCompleted, task_name=task_name)
            failed_layer = self.first(TaskFailed, task_name=task_name)

            if completed_layer:
                duration = getattr(completed_layer.effect, "duration_ms", 0)
                status = f"[completed in {duration:.1f}ms] OK"
            elif failed_layer:
                status = "[failed] FAILED"
            else:
                status = "[in progress]"

            lines.append(f"Task: {task_name} {status}")

            # Show effects for this task
            for shown, layer in enumerate(task_layers):
                if shown >= max_effects_per_task:
                    remaining = len(task_layers) - shown
                    lines.append(f"  ... {remaining} more effects")
                    break

                effect = layer.effect
                effect_type = effect.effect_type.replace("_", " ").title().replace(" ", "")

                # Build the line
                depth_indent = ""
                if show_nested and layer.scope_depth > 0:
                    depth_indent = "  " * layer.scope_depth

                # Add binding/context info if relevant
                extra_info = ""
                if hasattr(effect, "binding_name") and effect.binding_name:
                    extra_info = f" ({effect.binding_name})"
                elif hasattr(effect, "tool_name") and effect.tool_name:
                    extra_info = f" ({effect.tool_name})"

                # Mark failures
                fail_marker = ""
                if isinstance(effect, TaskFailed):
                    fail_marker = " FAILED"

                line = f"  #{layer.sequence:3d} {depth_indent}{effect_type}{extra_info}{fail_marker}"
                if show_nested:
                    line = f"{line:<55} depth={layer.scope_depth}"
                lines.append(line)

                # For TaskFailed, show error details
                if isinstance(effect, TaskFailed):
                    lines.append(f"       Error: {effect.error}")
                    if effect.error_location:
                        lines.append(f"       Location: {effect.error_location}")
                    if effect.session_id:
                        lines.append(f"       Session: {effect.session_id}")
                    if effect.last_tool_name:
                        lines.append(f"       Last tool: {effect.last_tool_name}")
                    if effect.suggestions:
                        lines.append("       Suggestions:")
                        for suggestion in effect.suggestions:
                            lines.append(f"         - {suggestion}")

            lines.append("")

        return "\n".join(lines)

    # --- Serialization ---

    def to_json(self, indent: int = 2) -> str:
        """Serialize stream to JSON string.

        The output is a **flat list** of effect dicts, NOT a nested structure.
        Each dict contains both layer metadata (prefixed with _) and effect fields.

        Returns:
            JSON string that can be loaded with `Stream.from_json()`.

        Example output format:
            [
              {
                "_sequence": 0,
                "_source_context": null,
                "_scope_id": "scope_abc123",
                "_scope_depth": 0,
                "effect_type": "task_started",
                "task_name": "FixBug",
                "timestamp": 1706820000.0,
                ...
              },
              {
                "_sequence": 1,
                ...
              }
            ]
        """
        return json.dumps(self.to_dicts(), indent=indent, default=str)

    def to_dicts(self) -> list[dict[str, Any]]:
        """Convert stream to list of dicts (includes layer metadata).

        Returns a flat list where each dict contains:
        - Layer metadata prefixed with `_`: `_sequence`, `_source_context`,
          `_scope_id`, `_scope_depth`
        - Effect fields at root level (from Pydantic's `model_dump()`)

        Returns:
            List of dicts suitable for JSON serialization or `from_dicts()`.
        """
        result = []
        for layer in self._layers:
            effect = layer.effect
            # Use Pydantic's model_dump for proper serialization
            effect_data = effect.model_dump()
            data = {
                "_sequence": layer.sequence,
                "_source_context": layer.source_context,
                "_scope_id": layer.scope_id,
                "_scope_depth": layer.scope_depth,
                **effect_data,
            }
            result.append(data)
        return result

    @classmethod
    def from_dicts(
        cls,
        data: list[dict[str, Any]],
        *,
        registry: EffectTypeRegistry | None = None,
    ) -> Stream:
        """Deserialize stream from list of dicts.

        Args:
            data: List of dicts, each containing layer metadata (prefixed with `_`)
                  and effect fields. Layer metadata fields:
                  - `_sequence`: Position in stream (defaults to list index if missing)
                  - `_source_context`: Context ID for filtering (optional)
                  - `_scope_id`: Scope ID that emitted this effect (optional)
                  - `_scope_depth`: Depth in scope hierarchy (defaults to 0)
            registry: Optional explicit registry snapshot for effect decode.

        Returns:
            Reconstructed Stream instance.

        Note:
            Unknown effect types are handled gracefully via `effect_from_dict()`.
            Missing `_sequence` values default to the dict's list index.
        """
        from shepherd_core.effects._decode import decode_effect

        layers = []
        for i, d in enumerate(data):
            d_copy = d.copy()
            sequence = d_copy.pop("_sequence", i)
            source_context = d_copy.pop("_source_context", None)
            scope_id = d_copy.pop("_scope_id", None)
            scope_depth = d_copy.pop("_scope_depth", 0)
            effect = decode_effect(d_copy, registry=registry)
            # If source_context not in serialized data, extract from effect
            if source_context is None:
                source_context = getattr(effect, "context_id", None)
            layers.append(
                EffectLayer(
                    effect=effect,
                    sequence=sequence,
                    source_context=source_context,
                    scope_id=scope_id,
                    scope_depth=scope_depth,
                )
            )
        return cls(_layers=tuple(layers))

    @classmethod
    def from_json(cls, json_str: str, *, registry: EffectTypeRegistry | None = None) -> Stream:
        """Deserialize stream from JSON string.

        IMPORTANT: The JSON format is a **flat list** of effect dicts, NOT a nested
        structure like `{"layers": [...]}`. Each dict contains:

        - Layer metadata (prefixed with `_`): `_sequence`, `_source_context`,
          `_scope_id`, `_scope_depth`
        - Effect fields at root level: `effect_type`, `task_name`, `timestamp`, etc.

        Args:
            json_str: JSON string produced by `stream.to_json()`.
            registry: Optional explicit registry snapshot for effect decode.

        Returns:
            Reconstructed Stream instance.

        Example:
            >>> json_str = '''[
            ...   {
            ...     "_sequence": 0,
            ...     "_source_context": null,
            ...     "_scope_id": null,
            ...     "_scope_depth": 0,
            ...     "effect_type": "task_started",
            ...     "task_name": "FixBug",
            ...     "timestamp": 1706820000.0
            ...   },
            ...   {
            ...     "_sequence": 1,
            ...     "effect_type": "tool_call_started",
            ...     "tool_call_id": "tc_001",
            ...     "tool_name": "read_file"
            ...   }
            ... ]'''
            >>> stream = Stream.from_json(json_str)
            >>> len(stream)
            2

        See Also:
            - `to_json()`: Serialize stream to this format.
            - `from_dicts()`: Load from already-parsed list of dicts.
        """
        data = json.loads(json_str)
        return cls.from_dicts(data, registry=registry)

    # --- Timeline (D45) ---

    def timeline_entries(
        self,
        *,
        include_types: set[type[Effect]] | None = None,
        exclude_types: set[type[Effect]] | None = None,
        where: Callable[[Effect], bool] | None = None,
    ) -> list[TimelineEntry]:
        """Get structured timeline entries for programmatic analysis.

        Returns a list of TimelineEntry objects with relative timestamps
        from the first effect in the stream.

        Args:
            include_types: Only include these effect types (default: all).
            exclude_types: Exclude these effect types (default: none).
            where: Optional predicate to filter effects.

        Returns:
            List of TimelineEntry objects with relative timestamps.

        Example:
            >>> entries = scope.effects.timeline_entries()
            >>> slow_effects = [e for e in entries if e.relative_seconds > 5.0]
        """
        if not self._layers:
            return []

        base_time = self._layers[0].effect.timestamp
        entries: list[TimelineEntry] = []

        for layer in self._layers:
            effect = layer.effect

            if include_types and type(effect) not in include_types:
                continue
            if exclude_types and type(effect) in exclude_types:
                continue
            if where and not where(effect):
                continue

            delta = effect.timestamp - base_time
            entries.append(TimelineEntry(relative_seconds=delta, effect=effect, layer=layer))

        return entries

    def timeline(
        self,
        *,
        include_types: set[type[Effect]] | None = None,
        exclude_types: set[type[Effect]] | None = None,
        where: Callable[[Effect], bool] | None = None,
    ) -> str:
        """Render a chronological view of effects for human consumption.

        Returns a formatted string showing timestamped effects,
        useful for debugging and understanding execution flow.

        Args:
            include_types: Only show these effect types (default: all).
            exclude_types: Hide these effect types (default: none).
            where: Optional predicate to filter effects.

        Returns:
            Formatted string showing timestamped effects.

        Example:
            >>> print(scope.effects.timeline())
            000.000s  TaskStarted
            000.123s  ToolCallStarted
            001.456s  ToolCallCompleted
            002.789s  TaskCompleted

            >>> print(scope.effects.timeline(include_types={ToolCallStarted, ToolCallCompleted}))
            000.123s  ToolCallStarted
            001.456s  ToolCallCompleted
        """
        entries = self.timeline_entries(
            include_types=include_types,
            exclude_types=exclude_types,
            where=where,
        )
        return "\n".join(str(entry) for entry in entries)

    def filter(self, predicate: Callable[[Effect], bool]) -> Stream:
        """Return new stream with only effects matching predicate.

        This is a more flexible filtering method that allows arbitrary
        predicates, complementing the attribution-based query methods.

        Args:
            predicate: Function that takes an Effect and returns True to keep.

        Returns:
            New Stream with only matching effects.

        Example:
            >>> large_patches = scope.effects.filter(lambda e: isinstance(e, FilePatch) and len(e.new_content) > 1000)
        """
        filtered_layers = tuple(layer for layer in self._layers if predicate(layer.effect))
        return Stream(
            _layers=filtered_layers,
            _scope_id=self._scope_id,
            _scope_depth=self._scope_depth,
        )

    # --- Views ---

    def intents(self) -> IntentsView:
        """View showing only intent effects (tool calls).

        Returns a lazy, reusable view filtering to ToolCallStarted,
        ToolCallCompleted, and ToolCallRejected effects.

        Example:
            for layer in stream.intents():
                print(layer.effect.tool_name)

            # Quick membership check
            if ToolCallRejected in stream.intents():
                print("Some tool calls were rejected")
        """
        from shepherd_core.effects.views import IntentsView

        return IntentsView(self)

    def outcomes(self, *, include_types: tuple[type[Effect], ...] | None = None) -> OutcomesView:
        """View showing effects representing external world interactions.

        Includes file operations (read, create, modify, delete), task outcomes
        (completed, failed), artifacts, and workspace patches.

        Args:
            include_types: Optional tuple of additional effect types to include.
                Use this to include custom domain effects.

        Example:
            # Standard outcomes
            for layer in stream.outcomes():
                print(layer.effect)

            # Include custom domain effects
            from my_domain import TransactionEffect
            outcomes = stream.outcomes(include_types=(TransactionEffect,))
        """
        from shepherd_core.effects.views import OutcomesView

        return OutcomesView(self, include_types=include_types)

    def costs(self) -> CostsView:
        """View focused on resource consumption metrics.

        Returns a view that can compute aggregated cost metrics via summarize().

        Example:
            costs = stream.costs().summarize()
            print(f"Tool calls: {costs.tool_calls}")
            print(f"Files modified: {len(costs.files_modified)}")
        """
        from shepherd_core.effects.views import CostsView

        return CostsView(self)

    def thinking(self) -> ThinkingView:
        """View showing agent reasoning (excludes prompts).

        Includes AgentThinking and AgentMessage effects. Excludes PromptSent
        as that's input to the agent, not its reasoning.

        Example:
            for layer in stream.thinking():
                print(layer.effect.content[:100])
        """
        from shepherd_core.effects.views import ThinkingView

        return ThinkingView(self)

    def as_causality_tree(self) -> CausalityTreeView:
        """View organizing effects by causality relationships.

        Builds a tree where ToolCallStarted effects contain their result
        effects as children, linked via tool_call_id.

        Example:
            tree = stream.as_causality_tree()
            for root in tree.as_tree():
                print(root.effect_type)
                for child in root.children:
                    print(f"  -> {child.effect_type}")
        """
        from shepherd_core.effects.views import CausalityTreeView

        return CausalityTreeView(self)

    def profile(self) -> ProfileView:
        """View for computing profiling metrics.

        Returns a view that can compute a structured ProfileSummary via summarize().

        Example:
            summary = stream.profile().summarize()
            print(f"Cost: ${summary.cost_summary.cost_usd}")
            print(f"Slowest tool: {summary.tools_by_duration[0].tool_name}")
        """
        from shepherd_core.effects.views import ProfileView

        return ProfileView(self)

    def to_profile(self, **options: Any) -> str:
        """Format stream as a profiling dashboard.

        Computes the full ProfileSummary and renders it as a compact
        80-column terminal dashboard.

        Args:
            **options: Toggle flags passed to format_profile()
                (show_tree, show_tools, show_phase_detail, show_bar_chart, show_turn_detail)

        Example:
            print(stream.to_profile())
            print(stream.to_profile(show_tree=False))
        """
        from shepherd_core.effects.formatters import format_profile

        summary = self.profile().summarize()
        return format_profile(summary, **options)

    def first_error(self) -> TaskFailed | None:
        """Get first TaskFailed effect, or None if no errors.

        Convenience method for the common pattern of checking for failures.

        Example:
            if error := stream.first_error():
                print(f"Failed: {error.error_type}: {error.error}")
        """
        from shepherd_core.effects import TaskFailed

        layer = self.first(TaskFailed)
        return layer.effect if layer else None  # type: ignore[return-value]

    # --- Formatters ---

    def to_markdown(self, **options: Any) -> str:
        """Format stream as structured markdown.

        Produces a readable summary with sections for:
        - Execution summary (status, duration, tool calls)
        - Timeline table
        - Files accessed
        - Errors (if any)
        - Agent reasoning

        Args:
            **options: Passed to FormatterOptions (max_effects, include_timestamps, etc.)

        Example:
            print(stream.to_markdown())
            print(stream.to_markdown(max_effects=20, include_timestamps=False))
        """
        from shepherd_core.effects.formatters import FormatterOptions, MarkdownFormatter

        return MarkdownFormatter().format_stream(self, FormatterOptions(**options))

    def to_compact(self, **options: Any) -> str:
        """Format stream as compact log lines (one per effect).

        Produces grep-friendly output like:
            [0.000] TaskStarted task=FixBug
            [0.123] ToolCallStarted tool=read_file path=src/auth.py

        Args:
            **options: Passed to FormatterOptions

        Example:
            print(stream.to_compact())
        """
        from shepherd_core.effects.formatters import CompactFormatter, FormatterOptions

        return CompactFormatter().format_stream(self, FormatterOptions(**options))

    def to_tree(self, **options: Any) -> str:
        """Format stream as causality tree.

        Produces a tree showing effect relationships:
            TaskStarted: FixBug
            +-- ToolCallStarted: read_file
            |   +-- FileRead: src/auth.py
            +-- TaskCompleted: FixBug (1.23s)

        Args:
            **options: Passed to FormatterOptions

        Example:
            print(stream.to_tree())
        """
        from shepherd_core.effects.formatters import FormatterOptions, TreeFormatter

        return TreeFormatter().format_stream(self, FormatterOptions(**options))

    # --- Display ---

    def summary(self) -> str:
        """Return a summary of the stream contents."""
        lines = [
            f"Stream with {len(self)} effects:",
            f"  Tasks: {sorted(self.tasks())}",
            f"  Providers: {len(self.providers())}",
            f"  Contexts: {len(self.contexts())}",
            f"  Effect types: {sorted(self.effect_types())}",
        ]
        return "\n".join(lines)

    def __str__(self) -> str:
        """Return string representation with context breakdown if multiple contexts."""
        contexts = self.contexts()
        none_count = sum(1 for layer in self._layers if layer.source_context is None)

        if len(contexts) <= 1 and none_count == 0:
            return f"Stream({len(self)} effects)"

        # Multiple contexts or mix of None and contexts - show breakdown
        lines = [f"Stream({len(self)} effects)"]
        lines.append("Contexts:")

        if none_count > 0:
            lines.append(f"  (no context): {none_count} effects")

        for ctx in sorted(contexts):
            count = sum(1 for layer in self._layers if layer.source_context == ctx)
            lines.append(f"  {ctx}: {count} effects")

        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"Stream({len(self)} effects)"


__all__ = ["EffectLayer", "Stream", "TimelineEntry"]
