# Feature: {{feature_name}}

## Overview

{{feature_description}}

## TDD Approach

Follow the Red-Green-Refactor cycle:

1. **Red**: Write a failing test that defines expected behavior
2. **Green**: Write minimal code to make the test pass
3. **Refactor**: Clean up while keeping tests green

## Acceptance Criteria

{{acceptance_criteria}}

## Implementation Steps

1. **Create test file** in the appropriate `tests/` directory
2. **Write failing tests** that define the expected behavior
3. **Run tests** to confirm they fail (Red)
4. **Implement the feature** with minimal code to pass tests
5. **Run tests** to confirm they pass (Green)
6. **Refactor** if needed, ensuring tests stay green
7. **Update documentation** if the feature is user-facing

## Deliverables

- [ ] Test file created with comprehensive test cases
- [ ] Feature implementation complete
- [ ] All tests passing
- [ ] Code follows existing project patterns
- [ ] Documentation updated (if applicable)

## Notes

- Start with the simplest test case
- Each test should verify one specific behavior
- Keep implementation minimal - just enough to pass tests
- Refactor only when tests are green
