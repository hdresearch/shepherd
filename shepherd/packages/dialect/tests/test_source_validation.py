"""W3a re-pins — source validation as the advisory filter (authoring re-pin plan).

The D2 security guarantee, re-pinned onto the dependency-free ``ast`` filter.
The legacy ``test_secure.py::TestSecurityBlocking`` rows (block os/subprocess/
eval/exec/dunder/open/builtins/getattr) re-pin here verbatim in intent — same
patterns refused, now as advisory findings (and jail-enforced at runtime).

RETIRED with rationale (the RestrictedPython reshape — confinement-compiler.md
``source-validation-is-advisory-the-jail-enforces``): ``test_secure.py::
TestLegitimatePatterns`` (6 rows — reconstruct-and-run a legitimate task
*class* under an in-process RestrictedPython sandbox). The dialect resolves
tasks by
``task_id`` import, not by reconstructing class source; untrusted-source
*execution* is contained by the jail, not an in-process sandbox. The
class-form reconstruction machinery does not port.
"""

import pytest

from shepherd_dialect.source_validation import (
    SourceValidationError,
    check_task_source,
    validate_task_source,
)

CLEAN = "def t(x: str) -> str:\n    return x.upper()\n"
IMPORT_OS = "def t():\n    import os\n    os.system('rm -rf /')\n"
IMPORT_SUBPROCESS = "def t():\n    import subprocess\n    subprocess.run(['x'])\n"
EVAL_ESCAPE = "def t():\n    return eval('1+1')\n"
EXEC_ESCAPE = "def t():\n    exec('x = 1')\n"
OPEN_FILE = "def t():\n    return open('/etc/passwd').read()\n"
BUILTINS_ESCAPE = "def t():\n    import builtins\n    return builtins.open('/etc/passwd').read()\n"
GETATTR_ESCAPE = "def t(self):\n    return getattr(self, '__class__').__bases__\n"
DUNDER_ACCESS = "def t(self):\n    return self.__class__.__mro__\n"


class TestLegitimateSourcePasses:
    def test_clean_source_has_no_violations(self):
        assert validate_task_source(CLEAN) == []

    def test_clean_source_passes_the_gate(self):
        check_task_source(CLEAN)  # does not raise

    def test_task_with_complex_types_is_clean(self):
        src = "from datetime import datetime\n\ndef t(d: datetime) -> int:\n    return d.year\n"
        assert validate_task_source(src) == []


class TestSecurityBlocking:
    """The D2 guarantee — same patterns the legacy RestrictedPython path blocked,
    now refused by the advisory filter (and jail-enforced at runtime)."""

    def test_blocks_import_os(self):
        assert any("os" in v for v in validate_task_source(IMPORT_OS))

    def test_blocks_import_subprocess(self):
        assert any("subprocess" in v for v in validate_task_source(IMPORT_SUBPROCESS))

    def test_blocks_eval(self):
        assert any("eval" in v for v in validate_task_source(EVAL_ESCAPE))

    def test_blocks_exec(self):
        assert any("exec" in v for v in validate_task_source(EXEC_ESCAPE))

    def test_blocks_dunder_access(self):
        assert any("__mro__" in v for v in validate_task_source(DUNDER_ACCESS))

    def test_blocks_open_file(self):
        assert any("open" in v for v in validate_task_source(OPEN_FILE))

    def test_blocks_builtins_escape(self):
        assert validate_task_source(BUILTINS_ESCAPE)  # import builtins + .open

    def test_blocks_getattr_escape(self):
        assert any("__class__" in v or "__bases__" in v for v in validate_task_source(GETATTR_ESCAPE))

    @pytest.mark.parametrize("bad", [IMPORT_OS, EVAL_ESCAPE, OPEN_FILE, GETATTR_ESCAPE])
    def test_gate_raises_on_dangerous_source(self, bad):
        with pytest.raises(SourceValidationError) as exc:
            check_task_source(bad)
        assert exc.value.violations


class TestEdgeCases:
    def test_empty_source_is_clean(self):
        assert validate_task_source("") == []

    def test_whitespace_only_is_clean(self):
        assert validate_task_source("   \n\n  ") == []

    def test_syntax_error_is_a_violation(self):
        violations = validate_task_source("def t(:\n  pass")
        assert violations
        assert "Syntax error" in violations[0]

    def test_non_strict_allows_pathlib(self):
        src = "import pathlib\n"
        assert validate_task_source(src, strict=True)
        assert validate_task_source(src, strict=False) == []
