"""Runs the quickstart exactly as documented; asserts the printed output."""


def test_quickstart_runs_and_prints_summary(capsys):
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).with_name("hello.py")), run_name="__main__")
    out = capsys.readouterr().out
    assert "typed Python functions" in out and out.count("- ") >= 3
