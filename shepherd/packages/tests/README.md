# shepherd-tests

Shared test utilities for Shepherd packages.

## Overview

This package provides common testing infrastructure used across all Shepherd packages:

- **Base test classes** for providers and contexts
- **Common pytest fixtures** for scopes, providers, and contexts
- **VCR utilities** for recording and replaying API interactions
- **Mock utilities** for testing without external dependencies

## Installation

```bash
pip install shepherd-tests

# With VCR support for recording API calls
pip install shepherd-tests[vcr]
```

## Usage

### Base Provider Tests

All provider implementations should pass the standard test suite:

```python
from shepherd_tests.base import BaseProviderTests
from shepherd_providers.claude import ClaudeProvider

class TestClaudeProvider(BaseProviderTests):
    def get_provider(self):
        return ClaudeProvider(name="test")

    # Add provider-specific tests
    def test_claude_thinking_mode(self):
        ...
```

### Base Context Tests

All context implementations should pass the standard test suite:

```python
from shepherd_tests.base import BaseContextTests
from shepherd_contexts.workspace import WorkspaceRef

class TestWorkspaceRef(BaseContextTests):
    def get_context(self):
        return WorkspaceRef.from_path("/tmp/test-repo")
```

### Common Fixtures

```python
# In your conftest.py
from shepherd_tests.conftest import *

# Now you have access to:
# - mock_provider: Pre-configured MockProvider
# - test_scope: Fresh Scope for each test
# - temp_workspace: Temporary git workspace
```

### VCR Cassettes

Record and replay API interactions for deterministic tests:

```python
import pytest
from shepherd_tests.vcr_utils import shepherd_vcr

@pytest.mark.vcr()
def test_claude_simple_task(claude_provider):
    result = claude_provider.execute_sdk(prompt="Hello", ...)
    assert result.output is not None
```

## License

MIT
