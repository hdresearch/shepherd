# Evaluation Scenarios

A library of reproducible test scenarios for shepherd evaluation and development.

## Quick Start

```bash
# List available bases and scenarios
uv run python shepherd/eval/generate.py list bases
uv run python shepherd/eval/generate.py list scenarios

# Generate a scenario workspace
uv run python shepherd/eval/generate.py scenario rich-cli/fix_bug ./workspace

# Checkout the scenario branch
cd workspace && git checkout bugfix/unicode-handling
```

For direct Python helper usage, switch to the `shepherd/` project root first:

```bash
cd shepherd
uv run python
```

```python
from examples.utils import generate_scenario_workspace
workspace_path = generate_scenario_workspace("rich-cli/fix_bug")
```

## Directory Structure

```
shepherd/eval/
├── library.yaml              # Registry of bases and templates
├── bases/                    # Base projects
│   └── rich-cli/
│       ├── base.yaml         # Project configuration
│       ├── base/             # Source files snapshot
│       ├── history/          # Commit history patches
│       ├── scenarios/        # Project-specific scenarios
│       │   ├── implement_feature/
│       │   ├── fix_bug/
│       │   ├── code_review/
│       │   └── refactor/
│       └── contexts/         # Example context files
├── templates/                # Generic scenario templates
│   └── tdd_feature/
├── generate.py               # Generator CLI
├── _lib.py                   # Library internals
└── tests/                    # Library tests
```

## Commands

### List Available Resources

```bash
# List base projects
uv run python shepherd/eval/generate.py list bases

# List all scenarios (project-specific + templates)
uv run python shepherd/eval/generate.py list scenarios

# List scenarios for a specific base
uv run python shepherd/eval/generate.py list scenarios --base rich-cli
```

### Generate Workspaces

```bash
# Generate base project only (main branch)
uv run python shepherd/eval/generate.py base rich-cli ./workspace

# Generate base + scenario branch
uv run python shepherd/eval/generate.py scenario rich-cli/fix_bug ./workspace

# Use --force to overwrite existing directory
uv run python shepherd/eval/generate.py scenario rich-cli/fix_bug ./workspace --force
```

### Apply Templates

Templates are generic scenarios that can be applied to any existing project:

```bash
# Apply TDD feature template
uv run python shepherd/eval/generate.py template tdd_feature ./my-project \
    --param feature_name="Add caching" \
    --param feature_description="In-memory cache for API responses" \
    --param acceptance_criteria="- Cache hits return in <1ms"
```

### Validate Library

```bash
# Check library structure and configurations
uv run python shepherd/eval/generate.py validate
```

## Available Scenarios

### rich-cli Base

| Scenario | Branch | Description |
|----------|--------|-------------|
| `implement_feature` | `feature/add-csv-export` | Add CSV output format support |
| `fix_bug` | `bugfix/unicode-handling` | Fix unicode encoding in file output |
| `code_review` | `review/add-quiet-mode` | Review PR with intentional issues |
| `refactor` | `refactor/split-formatters` | Extract formatter logic to module |

### Templates

| Template | Description |
|----------|-------------|
| `tdd_feature` | TDD-based feature implementation guide |

## Adding New Bases

1. Create directory structure:
   ```
   shepherd/eval/bases/<name>/
   ├── base.yaml
   ├── base/
   ├── history/
   └── scenarios/
   ```

2. Add to `shepherd/eval/library.yaml`:
   ```yaml
   bases:
     <name>:
       path: bases/<name>
       description: "..."
       language: python
   ```

3. Run `uv run python shepherd/eval/generate.py validate`

## Adding New Scenarios

1. Create scenario directory:
   ```
   shepherd/eval/bases/<base>/scenarios/<name>/
   ├── scenario.yaml
   ├── description.md  # or design.md, instructions.md
   └── *.patch         # optional patches
   ```

2. Define `scenario.yaml`:
   ```yaml
   type: project_specific
   base: <base>

   branch:
     name: <branch-name>
     from: main

   scenario:
     name: <name>
     category: fix_bug  # or implement_feature, code_review, refactor
     docs: [description.md]
     patches: []  # or list of patch files
   ```

3. Run `uv run python shepherd/eval/generate.py validate`

## Using with Shepherd

The evaluation scenarios integrate with shepherd via `shepherd/examples/utils.py`.
Run shell commands from the repository root, but switch into `shepherd/` for direct
Python helper imports:

```bash
cd shepherd
uv run python
```

```python
from examples.utils import generate_scenario_workspace, workspace_example
from shepherd import WorkspaceRef

# Simple usage
workspace_path = generate_scenario_workspace("rich-cli/fix_bug")
workspace = WorkspaceRef.from_path(workspace_path, branch="bugfix/unicode-handling")

# Or use the context manager
with workspace_example("Fix Bug Example", scenario="rich-cli/fix_bug") as workspace:
    result = FixBugTask(workspace=workspace, ...)
```
