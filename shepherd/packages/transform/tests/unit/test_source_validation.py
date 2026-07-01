"""Tests for transform-owned source validation helpers."""

from __future__ import annotations

from shepherd_transform.source import validate_task_source


class TestValidateTaskSource:
    """Tests for validate_task_source."""

    def test_valid_source_returns_empty(self):
        valid_source = """
@task
class ValidTask(BaseModel):
    query: Input(str)
    answer: Output(str)
"""
        violations = validate_task_source(valid_source)
        assert violations == []

    def test_import_os_blocked(self):
        malicious = """
import os
@task
class Evil(BaseModel):
    x: Input(str)
"""
        violations = validate_task_source(malicious)
        assert len(violations) > 0
        assert any("os" in v for v in violations)

    def test_import_subprocess_blocked(self):
        malicious = """
import subprocess
@task
class Evil(BaseModel):
    x: Input(str)
"""
        violations = validate_task_source(malicious)
        assert len(violations) > 0
        assert any("subprocess" in v for v in violations)

    def test_eval_call_blocked(self):
        malicious = """
@task
class Evil(BaseModel):
    x: Input(str)

    def execute(self):
        eval("print('pwned')")
"""
        violations = validate_task_source(malicious)
        assert len(violations) > 0
        assert any("eval" in v for v in violations)

    def test_exec_call_blocked(self):
        malicious = """
@task
class Evil(BaseModel):
    x: Input(str)

    def execute(self):
        exec("print('pwned')")
"""
        violations = validate_task_source(malicious)
        assert len(violations) > 0
        assert any("exec" in v for v in violations)

    def test_open_call_blocked(self):
        malicious = """
@task
class Evil(BaseModel):
    x: Input(str)

    def execute(self):
        open("/etc/passwd")
"""
        violations = validate_task_source(malicious)
        assert len(violations) > 0
        assert any("open" in v for v in violations)

    def test_dunder_attribute_blocked(self):
        malicious = """
@task
class Evil(BaseModel):
    x: Input(str)

    def execute(self):
        self.__class__.__bases__
"""
        violations = validate_task_source(malicious)
        assert len(violations) > 0
        assert any("__class__" in v or "__bases__" in v for v in violations)

    def test_syntax_error_reported(self):
        broken = "@task\nclass Foo(BaseModel)"
        violations = validate_task_source(broken)
        assert len(violations) > 0
        assert any("syntax" in v.lower() for v in violations)
