"""Foundation error types for scope operations.

These errors are part of the primitives layer and are used by
scope implementations to signal containment and operation errors.
"""

from __future__ import annotations


class ScopeError(Exception):
    """Error related to scope operations.

    Raised when:
    - Attempting to merge a discarded scope
    - Attempting to use a scope that has been closed
    - Invalid scope hierarchy operations
    """


class ContainmentError(ScopeError):
    """Error when effects have escaped containment.

    Raised when:
    - Attempting to discard after materialize()
    - Effects have already propagated and cannot be contained

    The containment model is:
        SANDBOX -> SCOPE -> MATERIALIZED -> ESCAPED

    Once effects escape (MATERIALIZED or ESCAPED), they cannot be
    discarded without explicit reversal operations.
    """


__all__ = ["ContainmentError", "ScopeError"]
