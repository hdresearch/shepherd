"""Quickstart example — tested in CI against the simulated offline provider."""

# --8<-- [start:hello]
import shepherd as shp
from shepherd.providers import claude


@shp.task
def summarize(article: str) -> str:
    """Summarize this article in three bullet points."""


with shp.workspace(model=claude("sonnet-4-5")):
    print(summarize("Shepherd is a Python framework for building agent systems..."))
# --8<-- [end:hello]
