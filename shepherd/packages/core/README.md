# shepherd-core

Semantic kernel for the Shepherd effect-based agent framework.

## Overview

`shepherd-core` owns the concepts required to define the system's semantics, and
nothing else:

- **Effects and streams** — immutable records of state changes, append-only
  streams, fold primitives
- **Provider protocol** — `Provider`, `EffectSink`, `ProviderRuntime`
- **Execution-context protocol** — `ExecutionContext` lifecycle
  (configure/prepare/extract/apply/cleanup)
- **Immutable scope substrate** — `ImmutableScope`, `ContextBinding`,
  `ContextRef`, `Stream`
- **Effect registry** — `EffectTypeRegistry`, `KERNEL_EFFECT_REGISTRY`,
  fail-closed contributor discovery

Runtime execution (`Scope`, `ExecutionLifecycle`, task/step authoring,
combinators, devices, handlers, cache, persistence, checkpoint,
materialization) lives in `shepherd-runtime`. Export/import lives in
`shepherd-export`. Testing helpers live in `shepherd-tests`.

## Installation

```bash
pip install shepherd-core
```

The only dependency is `pydantic>=2.0`.

For a working agent, also install the runtime and a provider:

```bash
pip install shepherd-runtime
pip install shepherd-providers   # Claude, OpenAI, etc.
pip install shepherd-contexts    # WorkspaceRef, SessionState, etc.
pip install shepherd-tests       # MockProvider and shared test helpers
```

## Quick Start

```python
from shepherd_core import Effect, Stream, ImmutableScope
from shepherd_core import ExecutionContext, Provider, ReversibilityLevel
from shepherd_core.foundation import fold, scan

# Runtime execution uses shepherd-runtime
from shepherd_runtime.scope import Scope
from shepherd_tests import MockProvider

with Scope() as scope:
    scope.register_provider("default", MockProvider(), default=True)
    # ... bind contexts and execute
```

## Core Invariant

All state is derived from immutable effects via the fold invariant:

```
state(t) = fold(apply_effect, effects[0:t], initial_state)
```

Contexts implement this through the v2 API:

- `extract_effects(sandbox, result)` — extract effects from execution (pure)
- `apply_effect(effect)` — derive new state from an effect (pure)

## Package Contents

### Kernel Abstractions

- `ImmutableScope` — the semantic center; immutable binding/state records
- `ExecutionContext` — protocol for stateful resources (workspaces, databases)
- `Provider` — abstract base for LLM SDK adapters
- `EffectSink` / `ProviderRuntime` — narrow provider execution contract
- `Effect` — immutable record of a state change
- `Stream` / `EffectLayer` — ordered, queryable effect sequence
- `ContextBinding` / `ContextRef` — binding identity and typed context lookup

### Types

- `ProviderBinding`, `ExecutionResult`, `ProviderCapabilities`
- `ReversibilityLevel`, `ToolCall`, `ToolResult`, `ToolDefinition`
- `TraceConfig`, `ValidationResult`

### Effects

- Task lifecycle: `TaskStarted`, `TaskCompleted`, `TaskFailed`
- Step lifecycle: `StepStarted`, `StepCompleted`, `StepFailed`
- Context lifecycle: `ContextConfigured`, `ContextPrepared`, `ContextCaptured`,
  `ContextCleanedUp`
- Tool effects: `ToolCallStarted`, `ToolCallCompleted`, `ToolCallRejected`
- File operations: `FileRead`, `FileCreate`, `FilePatch`, `FileDelete`
- Agent: `AgentMessage`, `AgentThinking`, `PromptSent`
- Data: `InputProvided`, `OutputProduced`, `ExternalAPICall`
- Pipeline stages: `StageStarted`, `StageCompleted`, `StageFailed`,
  `StageSkipped`

### Errors

- `ShepherdError`, `PreparationError`, `ExecutionError`,
  `ConfigurationError`, `ContainmentError`, `RollbackError`, and others

## Architecture

### Package Structure

```text
shepherd_core/
├── __init__.py            # Kernel public API
├── config.py              # Strict-mode toggle
├── constants.py           # Stable shared constants
├── errors.py              # Core error definitions
├── output.py              # Step output coercion and mock helpers
├── schema.py              # JSON Schema generation from Python types
├── text.py                # Text truncation helpers
├── types.py               # Shared core types (ProviderBinding, ToolCall, etc.)
├── foundation/            # Fold + protocols + irreducible semantics
├── effects/               # Effect definitions, registry, views, formatters
├── provider/              # Provider protocol + EffectSink/ProviderRuntime
├── context/               # Reduced kernel context protocol
├── scope/                 # Immutable scope substrate
└── _shared/               # Internal utilities (not public API)
```

### Scope Split

The `Scope` split is the most important boundary in the architecture.

**Kernel** (`shepherd_core.scope`) owns the immutable substrate:
`ImmutableScope`, `ContextBinding`, `ContextRef`, `Stream`, `EffectLayer`.

**Runtime** (`shepherd_runtime.scope`) owns the mutable execution shell:
`Scope`, `ScopeProxy`, `current_scope()`, `require_scope()`.

Also runtime-owned: `shepherd_runtime.materialization`,
`shepherd_runtime.checkpoint`, `shepherd_runtime.handlers`,
`shepherd_runtime.effect_materialization`.

### Design Principles

1. **Effect sourcing is the semantic core.** State is derived from immutable
   effects via the fold invariant. This is why `effects/`, `scope/stream.py`,
   and the fold substrate are kernel-owned.

2. **The mutable shell sits above the immutable substrate.** `ImmutableScope`
   is the kernel center. Runtime `Scope` is a shell built on that substrate.
   The kernel never depends on runtime.

3. **Effect registration is fail-closed.** The kernel owns
   `EffectTypeRegistry`. Runtime composes the full registry via
   `compose_effect_registry()`, discovering domain effect contributors through
   `shepherd.effects` entry points and rejecting duplicates or kernel-colliding
   keys.

4. **Provider contracts are narrow.** Providers receive `ProviderRuntime`
   (`emit(effect)` + `task_name`), not the full `Scope`.

## Import Guidance

```python
# Kernel imports
from shepherd_core import Effect, Stream, ImmutableScope, Provider
from shepherd_core import EffectSink, ProviderRuntime, ExecutionContext
from shepherd_core import ContextBinding, ContextRef, ReversibilityLevel
from shepherd_core.effects import EffectTypeRegistry, KERNEL_EFFECT_REGISTRY
from shepherd_core.foundation import fold, scan

# Runtime imports (NOT from shepherd_core)
from shepherd_runtime.scope import Scope
from shepherd_runtime.lifecycle import ExecutionLifecycle
from shepherd_runtime.task.authoring import task, Input, Output, Context, Artifact
from shepherd_runtime.step.api import step
from shepherd_runtime.combinators import gate, retry, parallel
from shepherd_runtime.device import Device
```

## License

MIT
