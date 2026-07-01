# Refactoring Task: Extract Output Formatters

## Background

The current `__main__.py` file contains all the output formatting logic
mixed with CLI argument handling. This makes it difficult to:

1. Test formatting logic in isolation
2. Add new output formats
3. Understand the code structure

## Objective

Extract the formatting logic into a separate `formatters.py` module with
a clean, extensible design.

## Requirements

### 1. Create `src/rich_cli/formatters.py`

Create a new module with:

```python
class Formatter:
    """Base class for output formatters."""
    def format(self, content: Any, console: Console) -> str:
        raise NotImplementedError

class JSONFormatter(Formatter):
    """Format output as JSON."""
    ...

class MarkdownFormatter(Formatter):
    """Format output as Markdown."""
    ...

class SyntaxFormatter(Formatter):
    """Format output with syntax highlighting."""
    ...
```

### 2. Update `__main__.py`

- Import formatters from the new module
- Replace inline formatting logic with formatter calls
- Keep CLI argument parsing in `__main__.py`

### 3. Maintain Backwards Compatibility

- All existing CLI commands must work identically
- No changes to command-line interface
- All existing tests must pass

## Constraints

- No new dependencies allowed
- Must maintain 100% backwards compatibility
- Existing tests must not be modified (they should still pass)

## Hints

1. Look for repeated patterns in how different content types are handled
2. The `--json`, `--markdown`, and syntax highlighting options are good
   candidates for extraction
3. Consider using a factory pattern to select formatters

## Success Criteria

- [ ] New `formatters.py` module created
- [ ] At least 3 formatter classes implemented
- [ ] `__main__.py` uses new formatters
- [ ] All existing tests pass without modification
- [ ] Code is cleaner and more modular

## Files to Create/Modify

- `src/rich_cli/formatters.py` - New file
- `src/rich_cli/__main__.py` - Updated to use formatters
- `tests/test_formatters.py` - Optional: new tests for formatters
