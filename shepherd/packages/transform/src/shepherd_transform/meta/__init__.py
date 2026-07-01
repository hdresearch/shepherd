"""Meta-tasks for reasoning about and transforming other tasks.

This module provides tasks that operate on other tasks:
- CritiqueTask: Analyze task design and suggest improvements
- TransformTask: Transform tasks based on natural language
- OptimizeFromEffects: Optimize based on execution feedback

Example:
    >>> from shepherd_transform.meta import TransformTask
    >>>
    >>> result = await scope.execute(
    ...     TransformTask(
    ...         target=MyTask,
    ...         instruction="Add input validation",
    ...     )
    ... )
    >>>
    >>> if result.verify_transformation(MyTask).passed:
    ...     NewTask = result.transformed

See Also:
    - shepherd_transform.source: Source extraction and reconstruction
    - shepherd_transform.grounding: Behavioral verification
"""

from __future__ import annotations

from .critique import CritiqueTask
from .optimize import OptimizeFromEffects
from .transform import TransformProposal, TransformTask, build_transform_proposal

__all__ = [
    "CritiqueTask",
    "OptimizeFromEffects",
    "TransformProposal",
    "TransformTask",
    "build_transform_proposal",
]
