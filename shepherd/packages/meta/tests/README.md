# Shepherd Test Suite

## Running Tests

```bash
# All tests
pytest shepherd/packages/meta/tests/

# Exclude slow integration tests
pytest shepherd/packages/meta/tests/ -m "not integration"

# Specific test file
pytest shepherd/packages/meta/tests/test_step.py -v

# With coverage
pytest shepherd/packages/meta/tests/ --cov=shepherd
```

## Test Organization

| File | Coverage |
|------|----------|
| `test_capabilities.py` | Tool capability validation |
| `test_capability_hooks.py` | Capability hook lifecycle |
| `test_context_id.py` | Context ID generation and correlation |
| `test_core.py` | Stream and effect basics |
| `test_email_validation.py` | Custom context example |
| `test_kvstore.py` | KVStoreContext lifecycle |
| `test_messages.py` | Message extraction from effects |
| `test_package_smoke.py` | Import verification |
| `test_protocol_compliance.py` | ExecutionContext protocol adherence |
| `test_reversibility.py` | Reversibility composition |
| `test_step.py` | @step decorator |
| `test_stream_queries.py` | Effect stream filtering |
| `test_task_execution.py` | @task decorator (comprehensive) |
| `test_verbose.py` | VerboseFormatter output |

## Directories

| Directory | Purpose |
|-----------|---------|
| `integration/` | End-to-end tests for three-layer architecture |
| `benchmarks/` | Performance tests |

## Fixtures (conftest.py)

- `MockProvider` — Deterministic provider for testing without API calls
- `mock_scope` — Pre-configured Scope with MockProvider
- Workspace fixtures for file operation tests

## Test Markers

```python
@pytest.mark.integration  # Slow, end-to-end tests
@pytest.mark.benchmark    # Performance tests
```

## Current Status

- **492+ tests passing**
- Comprehensive coverage of core functionality
- Mock mode support for fast, deterministic testing
