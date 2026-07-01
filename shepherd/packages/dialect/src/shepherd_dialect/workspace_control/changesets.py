"""Read-only Changeset wrapper for retained workspace outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shepherd_dialect.workspace_control.errors import WorkspaceControlError

if TYPE_CHECKING:
    from shepherd_dialect.workspace_control.run_outputs import RunOutput

JsonObject = dict[str, object]


@dataclass(frozen=True)
class ChangesetStat:
    """Small read-only summary of a run-output changeset."""

    output_id: str
    output_name: str
    binding: str
    state: str
    changed_path_count: int
    changed_paths: tuple[str, ...]

    def to_json(self) -> JsonObject:
        """Return a JSON-shaped changeset summary."""
        return {
            "output_id": self.output_id,
            "output_name": self.output_name,
            "binding": self.binding,
            "state": self.state,
            "changed_path_count": self.changed_path_count,
            "changed_paths": list(self.changed_paths),
        }


@dataclass(frozen=True, eq=False)
class Changeset:
    """Read-only public view of a retained workspace output's candidate delta.

    A Changeset is derived from a RunOutput. It does not own custody and cannot
    settle or mutate worlds; reads and inspections delegate through the output's
    retained-custody validation path.
    """

    _output: RunOutput = field(repr=False, compare=False)

    @property
    def output(self) -> RunOutput:
        """Return the output this read-only changeset view is derived from."""
        return self._output

    @property
    def output_id(self) -> str:
        return self._output.output_id

    @property
    def output_name(self) -> str:
        return self._output.output_name

    @property
    def binding(self) -> str:
        return self._output.binding

    @property
    def state(self) -> str:
        return self._output.state

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return self._output.changed_paths

    def refresh(self) -> Changeset:
        """Re-resolve the underlying output through its owning workspace."""
        return Changeset(self._output.refresh())

    def inspect(self) -> JsonObject:
        """Return a JSON-shaped, custody-refreshed changeset snapshot."""
        output = self._output.inspect()
        stat = _stat_from_output_snapshot(output)
        return {
            **stat.to_json(),
            "output": output,
        }

    def read_file(self, path: str) -> tuple[bytes, int] | None:
        """Read a file from the retained candidate artifact without selecting it."""
        return self._output.read_file(path)

    def stat(self) -> ChangesetStat:
        """Return a custody-refreshed changeset summary."""
        return _stat_from_output_snapshot(self._output.inspect())

    def to_json(self) -> JsonObject:
        """Return a JSON-shaped snapshot of this changeset wrapper."""
        return {
            "output_id": self.output_id,
            "output_name": self.output_name,
            "binding": self.binding,
            "state": self.state,
            "changed_path_count": len(self.changed_paths),
            "changed_paths": list(self.changed_paths),
            "output": self._output.to_json(),
        }


def _stat_from_output_snapshot(output: JsonObject) -> ChangesetStat:
    identity = output.get("identity")
    if not isinstance(identity, dict):
        raise WorkspaceControlError("run-output changeset snapshot is missing identity")
    output_id = identity.get("output_id")
    output_name = identity.get("output_name")
    binding = identity.get("binding")
    state = output.get("state")
    changed_paths = _changed_paths_from_output_snapshot(output.get("changed_paths"))
    if not isinstance(output_id, str) or not output_id:
        raise WorkspaceControlError("run-output changeset snapshot is missing output_id")
    if not isinstance(output_name, str) or not output_name:
        raise WorkspaceControlError("run-output changeset snapshot is missing output_name")
    if not isinstance(binding, str) or not binding:
        raise WorkspaceControlError("run-output changeset snapshot is missing binding")
    if not isinstance(state, str) or not state:
        raise WorkspaceControlError("run-output changeset snapshot is missing state")
    return ChangesetStat(
        output_id=output_id,
        output_name=output_name,
        binding=binding,
        state=state,
        changed_path_count=len(changed_paths),
        changed_paths=changed_paths,
    )


def _changed_paths_from_output_snapshot(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise WorkspaceControlError("run-output changeset snapshot is missing changed_paths")
    paths: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise WorkspaceControlError("run-output changeset snapshot has invalid changed_paths")
        paths.append(item)
    return tuple(paths)
