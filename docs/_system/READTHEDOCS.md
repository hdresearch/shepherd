# Hosting the Shepherd docs on ReadTheDocs

This system is MkDocs + mkdocstrings driven by `run.py`, so RTD uses `build.commands`
(`docs_system/.readthedocs.yaml`) to run the real pipeline instead of RTD's native
mkdocs builder, which would skip the generated API reference and the default-deny gate.

## One-time setup (once the repo is public)
1. readthedocs.org -> Import the repo (Community is free for public repos, like vLLM).
2. Admin -> Settings -> "Path for .readthedocs.yaml file" = `docs_system/.readthedocs.yaml`.
3. Build. RTD runs `run.py check` and publishes the PUBLIC build (`site/shepherd`).
   It never publishes the internal reviewer build (`site/internal`).

## Custom domain (docs.shepherd-agents.ai)
Admin -> Domains -> add `docs.shepherd-agents.ai` (canonical). RTD shows a CNAME
target (`<slug>.readthedocs.io`); add `CNAME docs -> <slug>.readthedocs.io` (DNS-only)
in Cloudflare. RTD issues SSL automatically.

## Speed note
`run.py check` also runs the example tests + the internal build. If RTD builds get
slow or flaky, add a lighter `run.py build` (regen + public strict build only) and call
that here instead of `check`.
