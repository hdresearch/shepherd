"""Public namespace for function-form task metadata markers.

The syntax nucleus keeps class-form field markers out of the top-level
public teaching surface. `InputMarker` remains available here for
`Annotated[...]` metadata on function-form task parameters.

See ``docs/design/proposed/260505-plans/DECISIONS.md`` D1.
"""

from __future__ import annotations

from shepherd_runtime.task.markers import InputMarker

__all__ = ["InputMarker"]
