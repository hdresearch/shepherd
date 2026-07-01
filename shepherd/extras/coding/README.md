# Shepherd Coding

Coding domain package for the Shepherd framework - provides GitHub integration and code review tasks for AI agents.

## Installation

```bash
pip install shepherd-coding

# With GitHub support
pip install shepherd-coding[github]
```

## Quick Start

```python
import asyncio

from shepherd import handle, workspace
from shepherd_core.schema import SINGLE_OUTPUT_KEY
from shepherd_runtime.provider_boundary import ModelResponse
from shepherd_coding import summarize


async def main() -> None:
    def fake_model(request):
        return ModelResponse(
            structured_output={
                SINGLE_OUTPUT_KEY: {
                    "summary": "No blocking findings.",
                    "score": 0.9,
                    "verdict": "APPROVE",
                }
            }
        )

    with workspace(model="offline-coding"), handle("model.call", fake_model):
        result = await summarize(
            findings_text="No findings.",
            file_change_summary="1 Python file changed.",
        )

    print(f"Score: {result.score}, Verdict: {result.verdict}")


asyncio.run(main())
```

The legacy PR workflow classes are still available for existing workflow
pipelines, but the current public migration path is the function-form task
surface.

```python
from shepherd_coding import GitHubContext
from shepherd_coding.tasks import configure_pr_review, summarize, validate_issue

github = GitHubContext(
    repo="owner/repo",
    token="github_pat_...",  # Optional: uses GITHUB_TOKEN or gh CLI
)
```

## Features

### Models

- `PRDetails`: Complete pull request information
- `PRFile`: Changed file details
- `PRCommit`: Commit information
- `PRReview`: Review details
- `PRAuthor`: Author information
- `PRLabel`: Label details

### Tasks

- Function-form: `summarize`, `validate_issue`, `generate_fix`,
  `critique_fix`, `configure_pr_review`, `configure_quality_gate`,
  `generate_pr_description`, and `run_linter`
- Legacy workflow-coupled: `FetchPR`, `ReviewPR`, `TriagePR`, and the
  quality-gate runner tasks remain available while the workflow pipelines
  migrate

### Effects

- `PRReviewSubmitted`: A review was submitted
- `PRCommented`: A comment was posted
- `PRMerged`: A PR was merged
- `PRClosed`: A PR was closed
- `PRLabeled`: Labels were added
- `PRUnlabeled`: Labels were removed

### Context

`GitHubContext` provides:
- Token resolution (explicit, env var, gh CLI)
- Repository inference from git remote
- Custom tools for PR operations
- Effect tracking for audit trails

## Authentication

Token resolution order:
1. Explicit token parameter
2. `GITHUB_TOKEN` environment variable
3. `gh auth token` CLI command

```python
# Explicit token
github = GitHubContext(repo="owner/repo", token="...")

# Environment variable
os.environ["GITHUB_TOKEN"] = "..."
github = GitHubContext(repo="owner/repo")

# gh CLI (must be logged in)
github = GitHubContext(repo="owner/repo")
```

## License

MIT
