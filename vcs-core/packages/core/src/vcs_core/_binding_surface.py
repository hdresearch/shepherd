"""Metadata-first read model for active substrate bindings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from vcs_core.discovery import BindingSpec
    from vcs_core.manifest import ImplementationKind, SubstrateManifest
    from vcs_core.types import BoundSubstrate

BindingSource = Literal["implicit-always", "implicit-auto-detect", "configured", "implicit-configured", "live"]
RegistrationSource = Literal["built-in", "plugin", "live"]


@dataclass(frozen=True)
class BindingSurfaceRecord:
    """One binding row suitable for listing without touching implementation code."""

    binding_name: str
    substrate_type: str
    implementation_kind: ImplementationKind
    binding_source: BindingSource
    configured: bool
    registration_source: RegistrationSource | None
    manifest: SubstrateManifest | None = None
    module_name: str | None = None
    class_name: str | None = None
    entry_point_name: str | None = None
    live: bool = False


class BindingSurface:
    """All-binding inventory plus lazy schema resolution.

    A2a deliberately makes this a read model only. Dispatch stays with
    ``VcsCore.exec`` / runtime-specific paths; the surface exposes no mutation
    or execution methods.
    """

    def __init__(
        self,
        *,
        specs: Iterable[BindingSpec] = (),
        live_bindings: Iterable[BoundSubstrate] = (),
    ) -> None:
        self._live_by_name = {binding.binding_name: binding for binding in live_bindings}
        self._records = _merge_records(specs, self._live_by_name)
        self._records_by_name = {record.binding_name: record for record in self._records}

    def records(self) -> tuple[BindingSurfaceRecord, ...]:
        """Return binding records in stable inventory order."""
        return self._records

    def names(self) -> tuple[str, ...]:
        """Return active binding names without loading schemas."""
        return tuple(record.binding_name for record in self._records)

    def get(self, name: str) -> BindingSurfaceRecord:
        """Resolve a binding record by exact binding name."""
        try:
            return self._records_by_name[name]
        except KeyError as exc:
            raise ValueError(f"Unknown binding '{name}'.") from exc


def _merge_records(
    specs: Iterable[BindingSpec],
    live_by_name: Mapping[str, BoundSubstrate],
) -> tuple[BindingSurfaceRecord, ...]:
    records: list[BindingSurfaceRecord] = []
    seen: set[str] = set()
    for spec in specs:
        seen.add(spec.binding_name)
        records.append(
            BindingSurfaceRecord(
                binding_name=spec.binding_name,
                substrate_type=spec.substrate_type,
                implementation_kind=spec.implementation_kind,
                binding_source=spec.binding_source,
                configured=spec.configured,
                registration_source=spec.registration_source,
                manifest=spec.manifest,
                module_name=spec.module_name,
                class_name=spec.class_name,
                entry_point_name=spec.entry_point_name,
                live=spec.binding_name in live_by_name,
            )
        )
    for binding in live_by_name.values():
        if binding.binding_name in seen:
            continue
        records.append(_record_from_live_binding(binding))
    return tuple(records)


def _record_from_live_binding(binding: BoundSubstrate) -> BindingSurfaceRecord:
    return BindingSurfaceRecord(
        binding_name=binding.binding_name,
        substrate_type=binding.substrate_type,
        implementation_kind="driver",
        binding_source="live",
        configured=bool(binding.config),
        registration_source="live",
        live=True,
    )
