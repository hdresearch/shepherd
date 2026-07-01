"""Git Effects Test Harness (Phase 0.5).

This package provides executable tests that validate the design decisions
documented in DESIGN-git-operation-effects.md and the Phase 2/3 spikes.

Test Status:
- Tests marked with pytest.mark.xfail will pass once implementation is complete
- Tests in test_git_state_reader.py should pass immediately (uses prototype)
- Tests in test_sha_translation.py should pass immediately (self-contained)

Structure:
- fixtures/: Prototype implementations and test data
- test_*.py: Executable tests validating design decisions
"""
