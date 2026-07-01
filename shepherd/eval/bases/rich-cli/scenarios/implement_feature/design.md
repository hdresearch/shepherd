# Feature: CSV Table Export

## Overview

Add support for exporting table output as CSV format. This allows users to pipe
rich-cli output to other tools or import into spreadsheets.

## Requirements

### Functional Requirements

1. Add a `--csv` flag to the CLI that outputs tables in CSV format
2. When `--csv` is used with JSON input containing tabular data, output CSV
3. CSV output should:
   - Include headers as the first row
   - Properly escape fields containing commas, quotes, or newlines
   - Use UTF-8 encoding

### Non-Functional Requirements

1. Use Python's built-in `csv` module (no new dependencies)
2. Maintain backwards compatibility (existing commands unchanged)
3. Add tests for the new functionality

## Technical Design

### CLI Changes

Add to `src/rich_cli/__main__.py`:

```python
@click.option("--csv", is_flag=True, help="Output tables as CSV format")
```

### Implementation Notes

- The `--csv` flag should work with JSON input that represents tabular data
- For non-tabular input, show an informative error message
- Consider how this interacts with existing `--json` flag

### Example Usage

```bash
# Input file: data.json
# [{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]

# Expected output:
rich data.json --csv
name,age
Alice,30
Bob,25
```

## Acceptance Criteria

- [ ] `rich data.json --csv` outputs valid CSV for array-of-objects JSON
- [ ] CSV output includes headers from object keys
- [ ] Special characters (commas, quotes, newlines) are properly escaped
- [ ] Non-tabular input shows helpful error message
- [ ] `--csv` flag is documented in `--help` output
- [ ] Unit tests added in `tests/test_csv.py`
- [ ] All existing tests continue to pass

## Testing

Create `tests/test_csv.py` with tests for:

1. Basic CSV output from JSON array
2. CSV escaping of special characters
3. Error handling for non-tabular input
4. Integration with other CLI options

## Files to Modify

- `src/rich_cli/__main__.py` - Add --csv option and handler
- `tests/test_csv.py` - New test file
