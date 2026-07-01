"""Private durable-locator helpers for query inventory probes."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Literal

from vcs_core._world_refs import is_ref_safe_component

LocatorEncoding = Literal["b64u", "sha256", "plain", "malformed"]


@dataclass(frozen=True)
class LocatorComponent:
    """Classification of one durable ref or file-name component."""

    raw_component: str
    encoding: LocatorEncoding
    decoded_value: str | None
    reversible: bool
    issue: str | None = None

    def to_fields(self, prefix: str) -> dict[str, object]:
        fields: dict[str, object] = {
            f"{prefix}_component": self.raw_component,
            f"{prefix}_encoding": self.encoding,
            f"{prefix}_reversible": self.reversible,
        }
        if self.decoded_value is not None:
            fields[f"{prefix}_decoded"] = self.decoded_value
        if self.issue is not None:
            fields[f"{prefix}_issue"] = self.issue
        return fields


def classify_locator_component(component: str) -> LocatorComponent:
    """Classify one durable locator component without trusting payload data."""
    if component.startswith("b64u_"):
        encoded = component.removeprefix("b64u_")
        if not encoded or any(
            char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for char in encoded
        ):
            return LocatorComponent(
                raw_component=component,
                encoding="malformed",
                decoded_value=None,
                reversible=False,
                issue="malformed_b64u_component",
            )
        try:
            padded = encoded + ("=" * (-len(encoded) % 4))
            decoded_bytes = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
            decoded = decoded_bytes.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return LocatorComponent(
                raw_component=component,
                encoding="malformed",
                decoded_value=None,
                reversible=False,
                issue="malformed_b64u_component",
            )
        if not decoded:
            return LocatorComponent(
                raw_component=component,
                encoding="malformed",
                decoded_value=None,
                reversible=False,
                issue="malformed_b64u_component",
            )
        canonical = base64.urlsafe_b64encode(decoded_bytes).decode("ascii").rstrip("=")
        if canonical != encoded:
            return LocatorComponent(
                raw_component=component,
                encoding="malformed",
                decoded_value=None,
                reversible=False,
                issue="malformed_b64u_component",
            )
        return LocatorComponent(
            raw_component=component,
            encoding="b64u",
            decoded_value=decoded,
            reversible=True,
        )
    if component.startswith("sha256_"):
        digest = component.removeprefix("sha256_")
        if len(digest) == 64 and all(char in "0123456789abcdef" for char in digest):
            return LocatorComponent(
                raw_component=component,
                encoding="sha256",
                decoded_value=None,
                reversible=False,
            )
        return LocatorComponent(
            raw_component=component,
            encoding="malformed",
            decoded_value=None,
            reversible=False,
            issue="malformed_sha256_component",
        )
    if is_ref_safe_component(component):
        return LocatorComponent(
            raw_component=component,
            encoding="plain",
            decoded_value=component,
            reversible=True,
        )
    return LocatorComponent(
        raw_component=component,
        encoding="malformed",
        decoded_value=None,
        reversible=False,
        issue="malformed_locator_component",
    )
