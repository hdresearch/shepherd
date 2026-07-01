"""``shepherd package`` — scaffold Shepherd extension packages."""

from __future__ import annotations

import importlib.resources
import re
import subprocess
import sys
from pathlib import Path

import click

from shepherd.cli._workspace_layout import (
    find_workspace_root,
    new_shepherd_package_dir,
    new_shepherd_workspace_member,
    workspace_member_covers,
)


def _to_module_name(name: str) -> str:
    """Convert a package name to a valid Python module name."""
    return re.sub(r"[^a-z0-9]", "_", name.lower()).strip("_")


def _to_title(name: str) -> str:
    """Convert a package name to a title-cased string suitable for class names."""
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", name).strip()
    return cleaned.title().replace(" ", "") if cleaned else "Package"


def _render_template(template_text: str, variables: dict[str, str]) -> str:
    """Render a template by substituting ``{{key}}`` placeholders."""
    result = template_text
    for key, value in variables.items():
        result = result.replace("{{" + key + "}}", value)
    return result


def _read_template(template_name: str) -> str:
    """Read a template file from the templates/package/ directory."""
    templates = importlib.resources.files("shepherd.templates.package")
    resource = templates.joinpath(template_name)
    return resource.read_text(encoding="utf-8")


@click.group(name="package")
def package() -> None:
    """Create and manage Shepherd extension packages."""


@package.command("init")
@click.argument("name")
@click.option("--description", "-d", default=None, help="Package description.")
def init(name: str, description: str | None) -> None:
    """Create a new Shepherd package.

    NAME is the package name (e.g., 'payments'). The package will be created
    under the current repo layout with a complete, installable skeleton.
    """
    module_name = _to_module_name(name)
    if not module_name or not module_name.isidentifier():
        raise click.UsageError(
            f"'{name}' converts to invalid Python module name '{module_name}'. "
            "Use a name containing ASCII letters (e.g., 'payments')."
        )
    title = _to_title(name)
    if description is None:
        description = f"{title} domain tasks for the Shepherd framework."
    keywords_str = f'["ai", "agents", "{name}", "llm"]'

    variables = {
        "name": name,
        "module_name": module_name,
        "title": title,
        "description": description,
        "keywords": keywords_str,
    }

    workspace_root = find_workspace_root()
    if workspace_root is not None:
        package_dir = new_shepherd_package_dir(workspace_root, name)
    else:
        package_dir = Path.cwd() / "packages" / f"shepherd-{name}"
    if package_dir.exists():
        click.echo(f"Error: {package_dir} already exists.", err=True)
        raise SystemExit(1)

    src_dir = package_dir / "src" / f"shepherd_{module_name}"
    test_dir = package_dir / "tests"
    src_dir.mkdir(parents=True)
    test_dir.mkdir(parents=True)

    templates = {
        "pyproject.toml.template": package_dir / "pyproject.toml",
        "__init__.py.template": src_dir / "__init__.py",
        "tasks.py.template": src_dir / "tasks.py",
        "test_tasks.py.template": test_dir / "test_tasks.py",
    }

    for template_name, output_path in templates.items():
        template_text = _read_template(template_name)
        rendered = _render_template(template_text, variables)
        output_path.write_text(rendered, encoding="utf-8")

    click.echo(f"Created package: {package_dir}")

    if workspace_root is not None:
        _add_to_workspace(workspace_root, name)
        click.echo("Added to UV workspace.")

        click.echo("Running uv sync...")
        result = subprocess.run(
            [sys.executable, "-m", "uv", "sync"],
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            click.echo("Package installed successfully.")
        else:
            click.echo(f"Warning: uv sync failed: {result.stderr}", err=True)
            click.echo("Run 'uv sync' manually to install the package.", err=True)

    click.echo(f"\nNext steps:\n  cd {package_dir}\n  Edit src/shepherd_{module_name}/tasks.py")


def _add_to_workspace(workspace_root: Path, name: str) -> None:
    """Add the new package to the UV workspace configuration."""
    pyproject_path = workspace_root / "pyproject.toml"
    content = pyproject_path.read_text(encoding="utf-8")

    package_path = new_shepherd_workspace_member(workspace_root, name)

    if not workspace_member_covers(workspace_root, package_path):
        members_pattern = r"(\[tool\.uv\.workspace\]\s*\nmembers\s*=\s*\[)"
        members_match = re.search(members_pattern, content)
        if members_match:
            insert_pos = members_match.end()
            content = content[:insert_pos] + f'\n    "{package_path}",' + content[insert_pos:]
        else:
            click.echo("Warning: could not find [tool.uv.workspace] members in pyproject.toml", err=True)

    source_key = f"shepherd-{name}"
    if not re.search(rf"^{re.escape(source_key)}\s*=", content, flags=re.MULTILINE):
        sources_pattern = r"(\[tool\.uv\.sources\])"
        sources_match = re.search(sources_pattern, content)
        if sources_match:
            insert_pos = sources_match.end()
            source_line = f"\n{source_key} = {{ workspace = true }}"
            content = content[:insert_pos] + source_line + content[insert_pos:]
        else:
            click.echo("Warning: could not find [tool.uv.sources] in pyproject.toml", err=True)

    pyproject_path.write_text(content, encoding="utf-8")


__all__ = ["init", "package"]
