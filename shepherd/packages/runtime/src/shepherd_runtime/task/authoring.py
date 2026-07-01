"""Public runtime task authoring owner paths."""

from __future__ import annotations

from .decorator import task
from .markers import (
    Artifact,
    ArtifactMarker,
    Check,
    CompletedTask,
    Context,
    ContextMarker,
    FileExists,
    Input,
    InputMarker,
    InRange,
    Infer,
    Matches,
    MaxLength,
    NonEmpty,
    Output,
    OutputMarker,
    TaskRef,
    _InferMarker,
)

task.__module__ = __name__

__all__ = [
    "Artifact",
    "ArtifactMarker",
    "Check",
    "CompletedTask",
    "Context",
    "ContextMarker",
    "FileExists",
    "InRange",
    "Infer",
    "Input",
    "InputMarker",
    "Matches",
    "MaxLength",
    "NonEmpty",
    "Output",
    "OutputMarker",
    "TaskRef",
    "task",
    "_InferMarker",
]
