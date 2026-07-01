"""Minimal vNext substrate contract and registry."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, Self

from ..kernel.facts import Record, RecordDraft, RecordId

if TYPE_CHECKING:
    from pathlib import Path

    from ..kernel.canonical import Containment

MaterializationOutcome = Literal["success", "clean_failure", "split_state"]
KV_SQLITE_SUBSTRATE_REF = "kv.sqlite.local.v1"
KV_PUT_DECLARATION_SCHEMA = "shepherd2.kv.put.v1"
KV_PUT_CAPTURE_SCHEMA = "shepherd2.kv.put.applied.v1"


class SubstrateError(RuntimeError):
    """Raised for substrate registration or dispatch failures."""


class UnknownSubstrateError(SubstrateError):
    """Raised when materialize dispatch names an unregistered substrate."""


@dataclass(frozen=True)
class MaterializationResult:
    """Substrate-produced result before kernel capture append."""

    outcome: MaterializationOutcome
    capture_drafts: tuple[RecordDraft, ...] = ()
    failure_reason: str = ""
    world_side_anchors: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class MaterializationReceipt:
    """Kernel materialize transition receipt."""

    outcome: MaterializationOutcome
    substrate_ref: str
    target_record_ids: tuple[RecordId, ...]
    produced_record_ids: tuple[RecordId, ...] = ()
    failure_reason: str = ""
    world_side_anchors: tuple[dict[str, Any], ...] = ()


class Substrate(Protocol):
    """Registered unit of declaration materialization."""

    substrate_ref: str
    declaration_schemas: frozenset[str]
    capture_schemas: frozenset[str]
    containment: Containment

    def materialize(self, records: tuple[Record, ...]) -> MaterializationResult: ...


class SubstrateRegistry:
    """Fail-closed process-local substrate registry."""

    def __init__(self) -> None:
        self._substrates: dict[str, Substrate] = {}

    def register(self, substrate: Substrate) -> None:
        existing = self._substrates.get(substrate.substrate_ref)
        if existing is not None and existing is not substrate:
            raise SubstrateError(f"substrate {substrate.substrate_ref!r} is already registered")
        self._substrates[substrate.substrate_ref] = substrate

    def get(self, substrate_ref: str) -> Substrate:
        try:
            return self._substrates[substrate_ref]
        except KeyError as exc:
            raise UnknownSubstrateError(f"unknown substrate: {substrate_ref}") from exc


@dataclass(frozen=True)
class EchoSubstrate:
    """Test substrate that turns declarations into same-schema captures."""

    substrate_ref: str
    declaration_schemas: frozenset[str]
    containment: Containment = "contained"

    @property
    def capture_schemas(self) -> frozenset[str]:
        return self.declaration_schemas

    def materialize(self, records: tuple[Record, ...]) -> MaterializationResult:
        captures = tuple(
            RecordDraft(
                mode="capture",
                schema_ref=record.envelope.schema_ref,
                kind_label=record.fact_kind,
                payload=dict(record.body.payload),
                caused_by_fact_ids=(record.envelope.record_id,),
            )
            for record in records
        )
        return MaterializationResult(outcome="success", capture_drafts=captures)


class SQLiteKVSubstrate:
    """Deterministic local key/value substrate backed by SQLite."""

    substrate_ref = KV_SQLITE_SUBSTRATE_REF
    declaration_schemas = frozenset({KV_PUT_DECLARATION_SCHEMA})
    capture_schemas = frozenset({KV_PUT_CAPTURE_SCHEMA})
    containment: Containment = "contained"

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._db = sqlite3.connect(self.path)
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL
            )
            """
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def get(self, key: str) -> Any:
        row = self._db.execute("SELECT value_json FROM kv WHERE key = ?", (key,)).fetchone()
        if row is None:
            return None
        return json.loads(str(row[0]))

    def materialize(self, records: tuple[Record, ...]) -> MaterializationResult:
        capture_drafts: list[RecordDraft] = []
        anchors: list[dict[str, Any]] = []
        with self._db:
            for record in records:
                key, value = _kv_put_payload(record)
                value_json = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
                self._db.execute(
                    """
                    INSERT INTO kv(key, value_json)
                    VALUES (?, ?)
                    ON CONFLICT(key)
                    DO UPDATE SET value_json = excluded.value_json
                    """,
                    (key, value_json),
                )
                capture_drafts.append(
                    RecordDraft(
                        mode="capture",
                        schema_ref=KV_PUT_CAPTURE_SCHEMA,
                        kind_label="kv_put_applied",
                        payload={"key": key, "value": value},
                        caused_by_fact_ids=(record.envelope.record_id,),
                    )
                )
                anchors.append({"kind": "kv_key", "key": key, "substrate_ref": self.substrate_ref})
        return MaterializationResult(
            outcome="success",
            capture_drafts=tuple(capture_drafts),
            world_side_anchors=tuple(anchors),
        )


def _kv_put_payload(record: Record) -> tuple[str, Any]:
    key = record.body.payload.get("key")
    if not isinstance(key, str) or not key:
        raise SubstrateError("kv put declarations require a non-empty string key")
    if "value" not in record.body.payload:
        raise SubstrateError("kv put declarations require a value")
    return key, record.body.payload["value"]
