# shepherd-transform

Advanced transformation and behavioral-grounding surfaces for Shepherd.

This package owns:

- meta-tasks such as `CritiqueTask`, `TransformTask`, and `OptimizeFromEffects`
- behavioral grounding and equivalence comparison helpers
- task input generation utilities used to verify transformations
- owner-path source extraction/reconstruction helpers for function-form callable-spine tasks

Typical imports:

```python
from shepherd_transform.meta import TransformTask
from shepherd_transform.grounding import EquivalenceLevel, behavioral_grounding
```

Function-form task source tooling stays under transform owner paths. It supports
actual callable task objects produced by the syntax nucleus; it does not add new
top-level `shepherd` facade names or name-keyed workflow lookup.

```python
from shepherd_runtime.nucleus import deliver, task
from shepherd_transform.source import extract_task_source, reconstruct_task


@task(guidance="Keep the answer brief.")
async def summarize(topic: str) -> str:
    return await deliver(str, goal=f"Summarize {topic}")


source = extract_task_source(summarize)
reconstructed = reconstruct_task(source)
```

`reconstruct_task_class(...)` remains class-form-only compatibility tooling.
Use `reconstruct_task(...)` for source that may contain a function-form `@task`.
