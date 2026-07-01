# `shepherd-ai` release packaging

This directory builds the public **`shepherd-ai`** distribution published to
[PyPI](https://pypi.org/project/shepherd-ai/).

## Why a single bundled wheel

The repository is a uv workspace of many small distributions (`shepherd`,
`shepherd-core`, `shepherd-runtime`, `shepherd-dialect`, `shepherd2`,
`vcs-core`, `commons-vcs`, ...). Publishing them individually is not viable:
several of those distribution names are already taken on PyPI by **unrelated**
projects — `shepherd` (v1.1.0), `shepherd-core` (an h5-dataform tool), and
`vcs-core` (a different VCS) — so a naive `pip install` of the closure would
resolve the wrong packages.

Instead the public release ships as **one self-contained wheel** named
`shepherd-ai` that vendors the entire runtime import closure. Users get the
documented experience:

```bash
pip install shepherd-ai        # deterministic quickstart surface
pip install "shepherd-ai[claude]"   # + Claude provider lane
import shepherd as sp
```

None of the internal distribution names are ever published, so the name
collisions are irrelevant. The import package names (`shepherd`,
`shepherd_core`, `vcs_core`, ...) are all distinct, so they co-locate cleanly.

## What is bundled

The 11 import packages in the `shepherd[providers,contexts]` runtime closure —
the deterministic quickstart plus the Claude/OpenAI provider lanes. The exact
list lives in `PACKAGES` in `build.py`.

Third-party runtime deps (`click`, `pydantic`, `pygit2`, `tomli-w`) come from
PyPI as normal. Provider SDKs are optional extras:

- `shepherd-ai[claude]` → `claude-agent-sdk`
- `shepherd-ai[openai]` → `openai`

## The entry-point gotcha

Shepherd discovers providers, contexts, effects, and VCS substrate plugins at
runtime through `importlib.metadata` entry points. Entry points are attached to
a **distribution**, so when the sub-packages collapse into one wheel, their
entry points must be re-declared in the single `shepherd-ai` metadata or plugin
discovery silently finds nothing. Those consolidated tables live in
`ENTRY_POINTS` in `build.py` and must stay in sync with the source packages'
`[project.entry-points.*]` tables:

| Group | Source package |
|-------|----------------|
| `shepherd.providers` | `shepherd/packages/providers` |
| `shepherd.contexts` / `shepherd.effects` | `shepherd/packages/contexts` |
| `vcscore.substrate_plugins` | `shepherd/packages/dialect` |

## Building & publishing

```bash
make build-dist     # -> dist/shepherd_ai-<version>.{tar.gz,whl}
make check-dist     # twine check
make publish-test   # upload to TestPyPI (dry run), then pip install from there
make publish        # upload to PyPI
```

`build.py` is non-destructive: it stages copies under `build/stage/` and never
mutates the workspace. It also rewrites the bundled `shepherd.__version__` to
match the release version so `import shepherd; shepherd.__version__` agrees with
the wheel version.

To cut a new version: `python packaging/shepherd-ai/build.py --version X.Y.Z`
(or edit `DEFAULT_VERSION` in `build.py`).
