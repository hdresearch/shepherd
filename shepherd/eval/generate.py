#!/usr/bin/env python3
"""Scenario Library Generator.

A unified CLI for generating test workspaces from the scenario library.

Commands:
    base <name> <target>              Generate base project only (main branch)
    scenario <id> <target>            Generate base + scenario branch
    template <name> <target>          Apply template to existing project
    list bases                        List available base projects
    list scenarios [--base NAME]      List available scenarios
    validate                          Validate library structure

Examples:
    # List available bases and scenarios (from the repository root)
    uv run python shepherd/eval/generate.py list bases
    uv run python shepherd/eval/generate.py list scenarios --base rich-cli

    # Generate a scenario workspace
    uv run python shepherd/eval/generate.py scenario rich-cli/fix_bug ./workspace

    # Generate just the base project
    uv run python shepherd/eval/generate.py base rich-cli ./workspace

    # Apply a template to an existing project
    uv run python shepherd/eval/generate.py template tdd_feature ./my-project \\
        --param feature_name="Add caching" \\
        --param feature_description="In-memory cache for API responses"
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from _lib import LibraryConfig, run_git


def cmd_base(args: argparse.Namespace) -> int:
    """Generate base project only (main branch)."""
    library = LibraryConfig.load()

    try:
        base_config = library.get_base(args.name)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    target = Path(args.target)
    if target.exists():
        if args.force:
            print(f"Removing existing directory: {target}")
            shutil.rmtree(target)
        else:
            print(f"Error: Target directory already exists: {target}", file=sys.stderr)
            print("Use --force to remove it, or choose a different target.")
            return 1

    print(f"Generating base project: {args.name}")
    print(f"  Target: {target}")

    try:
        base_config.generate(target, verbose=True)
        print()
        print(f"Generated base project at: {target}")
        print(f"  Base: {base_config.name} @ {base_config.commit[:12]}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_scenario(args: argparse.Namespace) -> int:
    """Generate base + scenario branch."""
    library = LibraryConfig.load()

    # Parse scenario ID
    if "/" not in args.scenario_id:
        print(
            f"Error: Invalid scenario ID: {args.scenario_id}",
            file=sys.stderr,
        )
        print("Expected format: 'base/scenario' (e.g., 'rich-cli/fix_bug')")
        return 1

    base_name, scenario_name = args.scenario_id.split("/", 1)

    try:
        base_config = library.get_base(base_name)
        scenario_config = base_config.get_scenario(scenario_name)
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    target = Path(args.target)
    if target.exists():
        if args.force:
            print(f"Removing existing directory: {target}")
            shutil.rmtree(target)
        else:
            print(f"Error: Target directory already exists: {target}", file=sys.stderr)
            print("Use --force to remove it, or choose a different target.")
            return 1

    print(f"Generating scenario: {args.scenario_id}")
    print(f"  Target: {target}")

    try:
        # Generate base project
        base_config.generate(target, verbose=True)

        # Apply scenario
        scenario_config.apply(target, verbose=True)

        # Return to main branch
        run_git(target, "checkout", "main")

        # Summary
        result = run_git(target, "branch", capture=True)
        branches = [b.strip().lstrip("* ") for b in result.stdout.strip().split("\n")]

        print()
        print(f"Generated scenario workspace at: {target}")
        print(f"  Base: {base_config.name} @ {base_config.commit[:12]}")
        print(f"  Branches: {', '.join(branches)}")
        print()
        print("Next steps:")
        print(f"  cd {target}")
        print(f"  git checkout {scenario_config.branch_name}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_template(args: argparse.Namespace) -> int:
    """Apply template to existing project."""
    library = LibraryConfig.load()

    try:
        scenario_config = library.get_scenario(f"template/{args.name}")
    except KeyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    target = Path(args.target)
    if not target.exists():
        print(f"Error: Target directory does not exist: {target}", file=sys.stderr)
        print("Templates are applied to existing projects.")
        return 1

    # Parse --param arguments
    params = {}
    for param in args.param or []:
        if "=" not in param:
            print(f"Error: Invalid param format: {param}", file=sys.stderr)
            print("Expected format: key=value")
            return 1
        key, value = param.split("=", 1)
        params[key] = value

    print(f"Applying template: {args.name}")
    print(f"  Target: {target}")

    try:
        scenario_config.apply(target, params=params, verbose=True)
        return 0
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_list(args: argparse.Namespace) -> int:
    """List bases or scenarios."""
    library = LibraryConfig.load()

    if args.what == "bases":
        bases = library.list_bases()
        if not bases:
            print("No bases available.")
            return 0

        print("Available bases:")
        for name in bases:
            try:
                base_config = library.get_base(name)
                desc = base_config.description.split("\n")[0][:60]
                print(f"  {name}: {desc}")
            except (KeyError, FileNotFoundError):
                print(f"  {name}: (config not found)")
        return 0

    elif args.what == "scenarios":
        scenarios = library.list_scenarios(base=args.base)
        if not scenarios:
            if args.base:
                print(f"No scenarios available for base: {args.base}")
            else:
                print("No scenarios available.")
            return 0

        print("Available scenarios:")
        for scenario_id in scenarios:
            print(f"  {scenario_id}")
        return 0

    else:
        print(f"Unknown list target: {args.what}", file=sys.stderr)
        return 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate library structure."""
    library = LibraryConfig.load()
    errors = []
    warnings = []

    print("Validating scenario library...")
    print()

    # Check bases
    print("Bases:")
    for name in library.list_bases():
        try:
            base_config = library.get_base(name)
            if not base_config.base_dir.exists():
                errors.append(f"  {name}: base/ directory missing")
            else:
                print(f"  {name}: OK")
        except FileNotFoundError as e:
            errors.append(f"  {name}: {e}")

    print()

    # Check scenarios
    print("Scenarios:")
    for scenario_id in library.list_scenarios():
        try:
            scenario_config = library.get_scenario(scenario_id)
            # Check docs exist
            missing_docs = []
            for doc in scenario_config.docs:
                if not (scenario_config.path / doc).exists():
                    missing_docs.append(doc)
            if missing_docs:
                warnings.append(f"  {scenario_id}: missing docs: {missing_docs}")
            else:
                print(f"  {scenario_id}: OK")
        except (KeyError, FileNotFoundError) as e:
            errors.append(f"  {scenario_id}: {e}")

    print()

    # Summary
    if errors:
        print("Errors:")
        for error in errors:
            print(f"  {error}")

    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"  {warning}")

    if not errors and not warnings:
        print("All validations passed!")
        return 0
    elif errors:
        return 1
    else:
        return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Scenario Library Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # base command
    base_parser = subparsers.add_parser(
        "base",
        help="Generate base project only",
    )
    base_parser.add_argument("name", help="Base project name (e.g., rich-cli)")
    base_parser.add_argument("target", help="Target directory")
    base_parser.add_argument(
        "--force", "-f", action="store_true", help="Remove target if exists"
    )

    # scenario command
    scenario_parser = subparsers.add_parser(
        "scenario",
        help="Generate base + scenario branch",
    )
    scenario_parser.add_argument(
        "scenario_id",
        help="Scenario ID (e.g., rich-cli/fix_bug)",
    )
    scenario_parser.add_argument("target", help="Target directory")
    scenario_parser.add_argument(
        "--force", "-f", action="store_true", help="Remove target if exists"
    )

    # template command
    template_parser = subparsers.add_parser(
        "template",
        help="Apply template to existing project",
    )
    template_parser.add_argument("name", help="Template name (e.g., tdd_feature)")
    template_parser.add_argument("target", help="Target directory (must exist)")
    template_parser.add_argument(
        "--param",
        "-p",
        action="append",
        help="Template parameter (key=value), can be repeated",
    )

    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="List bases or scenarios",
    )
    list_parser.add_argument(
        "what",
        choices=["bases", "scenarios"],
        help="What to list",
    )
    list_parser.add_argument(
        "--base",
        "-b",
        help="Filter scenarios by base (only for 'list scenarios')",
    )

    # validate command
    subparsers.add_parser(
        "validate",
        help="Validate library structure",
    )

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "base":
        return cmd_base(args)
    elif args.command == "scenario":
        return cmd_scenario(args)
    elif args.command == "template":
        return cmd_template(args)
    elif args.command == "list":
        return cmd_list(args)
    elif args.command == "validate":
        return cmd_validate(args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
