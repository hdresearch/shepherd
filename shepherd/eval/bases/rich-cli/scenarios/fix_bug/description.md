# Bug: File Output Encoding Issue

## Issue Summary

When using the `--output` option to write output to a file, unicode characters
are sometimes corrupted or cause errors, particularly on Windows systems or
when the system locale is not UTF-8.

## Bug ID

RICH-CLI-001

## Severity

Medium - Affects users with non-ASCII content

## Steps to Reproduce

1. Create a file with unicode content:
   ```bash
   echo '{"message": "Привет мир 你好世界"}' > unicode.json
   ```

2. Run rich-cli with file output:
   ```bash
   rich unicode.json --json --output result.txt
   ```

3. Check the output file:
   ```bash
   cat result.txt
   # Observe: Characters may be corrupted or command may fail
   ```

## Expected Behavior

Unicode characters should be preserved correctly in the output file,
regardless of system locale or platform.

## Actual Behavior

On some systems (particularly Windows with non-UTF-8 locale), the output
file contains corrupted characters or the command fails with an encoding error.

## Root Cause Analysis

The issue is in how files are opened for writing. The code uses the default
encoding (which varies by platform/locale) instead of explicitly specifying
UTF-8 encoding.

## Affected Code

Look in `src/rich_cli/__main__.py` for file output handling. The fix should
ensure UTF-8 encoding is used when writing to files.

## Hints

1. Search for `open(` calls that write to files
2. Look for the `--output` option handling
3. The fix is typically: `open(path, "w", encoding="utf-8")`

## Verification

After fixing:
1. The failing test should pass
2. All existing tests should continue to pass
3. Manual testing with unicode content should work
