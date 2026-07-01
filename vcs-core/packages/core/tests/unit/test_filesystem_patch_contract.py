"""Contract tests for filesystem Python interception declarations."""

from __future__ import annotations

from pathlib import Path

from vcs_core._substrate_runtime import BuiltInSubstrateContext
from vcs_core.store import Store
from vcs_core.substrates import FilesystemSubstrate

EXPECTED_FILESYSTEM_PATCH_TARGETS = {
    "builtins.open",
    "io.open",
    "os.remove",
    "os.unlink",
    "os.chmod",
    "pathlib.Path.chmod",
    "os.rename",
    "os.replace",
    "shutil.copyfile",
    "shutil.copy2",
    "shutil.move",
    "shutil.rmtree",
}

EXPECTED_EXTERNAL_WRITE_TARGETS = {
    "os.remove",
    "os.unlink",
    "os.chmod",
    "pathlib.Path.chmod",
    "os.rename",
    "os.replace",
    "shutil.copyfile",
    "shutil.copy2",
    "shutil.move",
    "shutil.rmtree",
}


def test_filesystem_python_patch_targets_are_explicitly_classified(tmp_path: Path) -> None:
    store = Store(str(tmp_path / ".vcscore"))
    filesystem = FilesystemSubstrate(BuiltInSubstrateContext(store, workspace=tmp_path))

    patches = {patch.target: patch for patch in filesystem.python_patches()}

    assert set(patches) == EXPECTED_FILESYSTEM_PATCH_TARGETS
    for target in EXPECTED_EXTERNAL_WRITE_TARGETS:
        assert patches[target].mutation_intent == "external_write"

    assert callable(patches["builtins.open"].mutation_intent)
    assert callable(patches["io.open"].mutation_intent)
    assert patches["builtins.open"].mutation_intent(tmp_path / "tracked.txt", "r") == "none"
    assert patches["io.open"].mutation_intent(tmp_path / "tracked.txt", "rb") == "none"
    assert patches["builtins.open"].mutation_intent(tmp_path / "tracked.txt", "w") == "external_write"
    assert patches["io.open"].mutation_intent(tmp_path / "tracked.txt", "a+") == "external_write"
