"""commons_vcs — universal kernel for causally-closed version control.

Public API:

    Object, Edge, Resolver, Profile, Repo, Validator, Failure
        Kernel primitives. Failure is what validators return on
        rejection; Validator is the function-type alias.

    canonical_bytes, canonical_value_from_bytes, digest, CANONICAL_PREFIX
        commons.canonical.v1 byte-exact identity.

See README.md for usage and architecture.
"""

from ._types import Edge, Failure, FailureRecord, Object, Outcome, VerifyResult
from .canonical import CANONICAL_PREFIX, canonical_bytes, canonical_value_from_bytes, digest
from .kernel import (
    Profile,
    Repo,
    Resolver,
    Validator,
)

__all__ = [
    "CANONICAL_PREFIX",
    "Edge",
    "Failure",
    "FailureRecord",
    "Object",
    "Outcome",
    "Profile",
    "Repo",
    "Resolver",
    "Validator",
    "VerifyResult",
    "canonical_bytes",
    "canonical_value_from_bytes",
    "digest",
]
