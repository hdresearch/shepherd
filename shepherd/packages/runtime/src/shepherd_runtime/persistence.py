"""Public runtime persistence owner paths."""

from __future__ import annotations

from ._persistence_layers import layer_from_dict, layer_to_dict
from ._persistence_manager import PersistenceConfig, PersistenceManager
from ._persistence_project import ProjectId, ProjectMetadata
from ._persistence_stream import StreamId, StreamIndex, StreamMetadata
from ._persistence_writer import StreamReader, StreamWriter

_RUNTIME_OWNED = (
    PersistenceConfig,
    PersistenceManager,
    ProjectId,
    ProjectMetadata,
    StreamId,
    StreamIndex,
    StreamMetadata,
    StreamReader,
    StreamWriter,
    layer_from_dict,
    layer_to_dict,
)
for _symbol in _RUNTIME_OWNED:
    _symbol.__module__ = __name__

__all__ = [
    "PersistenceConfig",
    "PersistenceManager",
    "ProjectId",
    "ProjectMetadata",
    "StreamId",
    "StreamIndex",
    "StreamMetadata",
    "StreamReader",
    "StreamWriter",
    "layer_from_dict",
    "layer_to_dict",
]
