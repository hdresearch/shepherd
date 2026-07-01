# Shepherd Contexts

Generic execution contexts for the Shepherd framework.

## Installation

```bash
pip install shepherd-contexts
```

For optional dependencies:

```bash
# Git workspace support
pip install shepherd-contexts[workspace]

# Database support
pip install shepherd-contexts[database]

# All optional dependencies
pip install shepherd-contexts[all]
```

## Available Contexts

### WorkspaceRef

Git-backed workspace with capability-based access control.

```python
from shepherd_contexts.workspace import WorkspaceRef

workspace = WorkspaceRef.from_path("/path/to/repo")
workspace = workspace.with_bash()  # Enable bash capability
```

### SessionState

Invisible context for multi-turn conversation continuity.

```python
from shepherd_contexts.session import SessionState

session = SessionState()
# After first execution, session.session_id is populated
```

### MCPServerContext

External MCP server integration with zero-code configuration.

```python
from shepherd_contexts.mcp import MCPServerContext

# Simple stdio server
fs = MCPServerContext(
    name="filesystem",
    command="npx",
    args=("-y", "@modelcontextprotocol/server-filesystem", "/projects"),
)

# From config file
servers = MCPServerContext.from_yaml("mcp_servers.yaml")
```

### DatabaseContext

Read-only SQL database access with query validation.

```python
from shepherd_contexts.database import DatabaseContext

db = DatabaseContext(
    connection_string="postgresql://user:pass@localhost/mydb",
    database_name="mydb",
    allowed_tables=frozenset({"users", "orders"}),
    max_rows=1000,
)
```

### KVStoreContext

Simple key-value store for configuration and state management.

```python
from shepherd_contexts.kvstore import KVStoreContext

store = KVStoreContext.create({"user": "alice", "count": "0"})
```

### AppStoreContext

App Store Connect API access for reports and analytics.

```python
from shepherd_contexts.appstore import AppStoreContext

appstore = AppStoreContext(
    issuer_id="your-issuer-id",
    key_id="your-key-id",
    vendor_number="your-vendor-number",
)
```

## Effects

Each context module exports domain-specific effects:

- **workspace**: `WorkspacePatchCaptured`, `BashCommand`
- **session**: `SessionCreated`, `SessionForked`, `SessionResumed`
- **mcp**: `MCPServerConnected`, `MCPToolCalled`
- **database**: `QueryExecuted`
- **kvstore**: `KeySet`, `KeyDeleted`
- **appstore**: `AppStoreAPICall`

## v2 API

All contexts implement the v2 event-sourced API:

```python
# Extract effects from execution (PURE)
effects = context.extract_effects(sandbox, result)

# Derive new state from effect (PURE)
new_context = context.apply_effect(effect)
```

All contexts are frozen dataclasses (immutable). State changes return new instances.

## License

MIT
