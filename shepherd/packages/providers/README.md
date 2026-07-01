# shepherd-providers

Provider implementations for the Shepherd framework.

## Installation

```bash
pip install shepherd-providers[claude]  # For Claude provider
pip install shepherd-providers[all]     # All providers
```

## Usage

```python
from shepherd_providers.claude import ClaudeProvider
from shepherd_runtime.scope import Scope

provider = ClaudeProvider(name="default", model="claude-sonnet-4-20250514")

with Scope() as scope:
    scope.register_provider("default", provider, default=True)
    # ... execute tasks
```

## Providers

- **ClaudeProvider**: Claude Agent SDK adapter
- **OpenAIProvider**: OpenAI Agents SDK adapter (placeholder)
