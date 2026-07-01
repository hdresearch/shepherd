import ast
from pathlib import Path

BANNED_IMPORT_ROOTS = {
    "shepherd",
    "shepherd2",
    "anthropic",
    "boto3",
    "botocore",
    "cohere",
    "commons_vcs",
    "google",
    "vcs_core",
    "vcscore",
    "mistralai",
    "openai",
}


def test_semantic_core_has_no_carrier_storage_or_facade_imports() -> None:
    src_root = Path(__file__).parents[1] / "src" / "shepherd_kernel_v3_reference"
    offenders: list[str] = []

    for path in src_root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    _record_if_banned(offenders, path, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                _record_if_banned(offenders, path, node.module)

    assert offenders == []


def _record_if_banned(offenders: list[str], path: Path, module: str) -> None:
    root = module.split(".", maxsplit=1)[0]
    if root in BANNED_IMPORT_ROOTS:
        offenders.append(f"{path}: {module}")
