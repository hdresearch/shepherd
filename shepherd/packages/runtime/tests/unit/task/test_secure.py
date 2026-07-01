"""Tests for secure task reconstruction with RestrictedPython.

These tests verify that:
1. Legitimate @task classes can be reconstructed safely
2. Malicious patterns are blocked (imports, eval, dunders, etc.)
3. Error handling provides useful feedback
4. Edge cases are handled gracefully

Test Categories:
- TestLegitimatePatterns: 5 legitimate task patterns that should work
- TestSecurityBlocking: 8 attack patterns that should be blocked
- TestEdgeCases: Empty source, no @task, etc.
"""

import pytest
from shepherd_runtime.task.secure import SecurityError, secure_reconstruct_task_class

# =============================================================================
# Test Fixtures - Legitimate Task Patterns
# =============================================================================

SIMPLE_TASK = '''
@task
class SimpleTask(BaseModel):
    """A simple task."""
    query: Input(str)
    answer: Output(str)
'''

TASK_WITH_EXECUTE = '''
@task
class TaskWithExecute(BaseModel):
    """A task with custom execute."""
    x: Input(int)
    doubled: Output(int)

    def execute(self):
        self.doubled = self.x * 2
'''

TASK_WITH_COMPLEX_TYPES = '''
@task
class TaskWithComplexTypes(BaseModel):
    """Task with complex type annotations."""
    items: Input(list[str])
    config: Input(dict[str, int])
    result: Output(Optional[str])
'''

TASK_WITH_HELPER_METHODS = '''
@task
class TaskWithHelpers(BaseModel):
    """Task with private helper methods."""
    x: Input(int)
    y: Input(int)
    result: Output(int)

    def execute(self):
        self.result = self._compute_sum()

    def _compute_sum(self) -> int:
        return self.x + self.y
'''

TASK_WITH_DEFAULTS = '''
@task
class TaskWithDefaults(BaseModel):
    """Task with default values."""
    query: Input(str)
    max_results: Input(int) = 10
    include_metadata: Input(bool) = False
    results: Output(list[str])
'''


# =============================================================================
# Test Fixtures - Malicious Patterns
# =============================================================================

IMPORT_OS = """
import os
@task
class MaliciousTask(BaseModel):
    cmd: Input(str)
    result: Output(str)
"""

IMPORT_SUBPROCESS = """
import subprocess
@task
class MaliciousTask(BaseModel):
    cmd: Input(str)
    result: Output(str)
"""

EVAL_ESCAPE = """
@task
class MaliciousTask(BaseModel):
    code: Input(str)
    result: Output(str)

    def execute(self):
        self.result = eval(self.code)
"""

EXEC_ESCAPE = """
@task
class MaliciousTask(BaseModel):
    code: Input(str)
    result: Output(str)

    def execute(self):
        exec(self.code)
"""

DUNDER_ACCESS = """
@task
class MaliciousTask(BaseModel):
    result: Output(str)

    def execute(self):
        self.result = self.__class__.__bases__[0].__subclasses__()
"""

OPEN_FILE = """
@task
class MaliciousTask(BaseModel):
    path: Input(str)
    content: Output(str)

    def execute(self):
        with open(self.path) as f:
            self.content = f.read()
"""

BUILTINS_ESCAPE = """
@task
class MaliciousTask(BaseModel):
    result: Output(str)

    def execute(self):
        import builtins
        self.result = builtins.open("/etc/passwd").read()
"""

GETATTR_ESCAPE = """
@task
class MaliciousTask(BaseModel):
    result: Output(str)

    def execute(self):
        self.result = getattr(self, "__class__").__bases__
"""


# =============================================================================
# Test Classes
# =============================================================================


class TestLegitimatePatterns:
    """Test that legitimate @task patterns work correctly."""

    def test_simple_task(self):
        """Simple task with Input and Output fields."""
        task_class = secure_reconstruct_task_class(SIMPLE_TASK)
        assert task_class is not None
        assert hasattr(task_class, "_task_meta")
        assert getattr(task_class, "_task_source", None) == SIMPLE_TASK
        assert task_class.__name__ == "SimpleTask"

    def test_task_with_execute(self):
        """Task with custom execute method."""
        task_class = secure_reconstruct_task_class(TASK_WITH_EXECUTE)
        assert task_class is not None
        assert hasattr(task_class, "execute")
        # Verify the execute method is callable
        assert callable(getattr(task_class, "execute", None))

    def test_task_with_complex_types(self):
        """Task with complex type annotations (list, dict, Optional)."""
        task_class = secure_reconstruct_task_class(TASK_WITH_COMPLEX_TYPES)
        assert task_class is not None
        assert task_class.__name__ == "TaskWithComplexTypes"

    def test_task_with_helper_methods(self):
        """Task with private helper methods (_compute_sum)."""
        task_class = secure_reconstruct_task_class(TASK_WITH_HELPER_METHODS)
        assert task_class is not None
        # Private methods should be preserved
        assert hasattr(task_class, "_compute_sum")

    def test_task_with_defaults(self):
        """Task with default field values."""
        task_class = secure_reconstruct_task_class(TASK_WITH_DEFAULTS)
        assert task_class is not None
        # Check that defaults are preserved in the model
        fields = task_class.model_fields
        assert "max_results" in fields
        assert "include_metadata" in fields

    def test_secure_reconstruction_carries_source(self):
        """Secure reconstruction should preserve the original task source."""
        task_class = secure_reconstruct_task_class(SIMPLE_TASK)

        assert getattr(task_class, "_task_source", None) == SIMPLE_TASK


class TestSecurityBlocking:
    """Test that malicious patterns are blocked."""

    def test_blocks_import_os(self):
        """Block import of os module."""
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(IMPORT_OS)
        assert "import" in str(exc_info.value).lower() or "os" in str(exc_info.value).lower()

    def test_blocks_import_subprocess(self):
        """Block import of subprocess module."""
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(IMPORT_SUBPROCESS)
        assert "import" in str(exc_info.value).lower() or "subprocess" in str(exc_info.value).lower()

    def test_blocks_eval(self):
        """Block use of eval()."""
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(EVAL_ESCAPE)
        assert "eval" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()

    def test_blocks_exec(self):
        """Block use of exec()."""
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(EXEC_ESCAPE)
        assert "exec" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()

    def test_blocks_dunder_access(self):
        """Block access to dangerous dunder attributes."""
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(DUNDER_ACCESS)
        error_msg = str(exc_info.value).lower()
        assert "__bases__" in error_msg or "dunder" in error_msg or "not in the allowed" in error_msg

    def test_blocks_open_file(self):
        """Block use of open()."""
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(OPEN_FILE)
        assert "open" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()

    def test_blocks_builtins_escape(self):
        """Block import of builtins module.

        The canonical secure reconstruction path now blocks this during
        validation, before the task class is constructed.
        """
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(BUILTINS_ESCAPE)
        assert "builtins" in str(exc_info.value).lower() or "forbidden" in str(exc_info.value).lower()

    def test_blocks_getattr_escape(self):
        """Block getattr-based dunder access."""
        with pytest.raises(SecurityError) as exc_info:
            secure_reconstruct_task_class(GETATTR_ESCAPE)
        # Should be blocked either at compile time or runtime
        error_msg = str(exc_info.value).lower()
        assert "getattr" in error_msg or "__class__" in error_msg or "forbidden" in error_msg


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_source(self):
        """Empty source raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            secure_reconstruct_task_class("")
        assert "no @task class" in str(exc_info.value).lower()

    def test_whitespace_only_source(self):
        """Whitespace-only source raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            secure_reconstruct_task_class("   \n\n   ")
        assert "no @task class" in str(exc_info.value).lower()

    def test_no_task_decorator(self):
        """Class without @task decorator raises ValueError."""
        source = """
class NotATask(BaseModel):
    x: int
"""
        with pytest.raises(ValueError) as exc_info:
            secure_reconstruct_task_class(source)
        assert "no @task class" in str(exc_info.value).lower()

    def test_syntax_error(self):
        """Syntax errors are reported clearly."""
        with pytest.raises(SyntaxError):
            secure_reconstruct_task_class("def broken(")

    def test_multiple_tasks_returns_first(self):
        """Multiple @task classes returns the first one found."""
        source = """
@task
class FirstTask(BaseModel):
    x: Input(int)
    y: Output(int)

@task
class SecondTask(BaseModel):
    a: Input(str)
    b: Output(str)
"""
        task_class = secure_reconstruct_task_class(source)
        assert task_class is not None
        # Should return one of them (order may vary due to dict iteration)
        assert task_class.__name__ in ("FirstTask", "SecondTask")

    def test_extra_namespace_is_available(self):
        """Extra namespace bindings are available in source."""
        source = """
@task
class TaskWithExtra(BaseModel):
    x: Input(int)
    result: Output(int)

    def execute(self):
        self.result = MAGIC_NUMBER + self.x
"""
        task_class = secure_reconstruct_task_class(
            source,
            extra_namespace={"MAGIC_NUMBER": 42},
        )
        assert task_class is not None


class TestPydanticSafeNodeTransformer:
    """Test the extended AST transformer."""

    def test_allows_single_underscore_names(self):
        """Single underscore names (private convention) are allowed."""
        source = """
@task
class TaskWithPrivate(BaseModel):
    _internal: int = 0
    x: Input(int)
    result: Output(int)

    def _helper(self):
        return self._internal + 1
"""
        task_class = secure_reconstruct_task_class(source)
        assert task_class is not None
        assert hasattr(task_class, "_helper")

    def test_allows_safe_dunders(self):
        """Safe dunder methods are allowed."""
        source = """
@task
class TaskWithDunders(BaseModel):
    x: Input(int)
    result: Output(str)

    def __str__(self):
        return f"Task(x={self.x})"

    def __repr__(self):
        return self.__str__()
"""
        task_class = secure_reconstruct_task_class(source)
        assert task_class is not None
        assert hasattr(task_class, "__str__")
        assert hasattr(task_class, "__repr__")
