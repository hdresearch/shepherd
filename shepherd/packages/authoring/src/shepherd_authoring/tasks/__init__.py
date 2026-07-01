"""Design-refinement leaf tasks.

- ExtractPrinciples: Extract guiding principles from a design document
- DraftSpikePlan: Draft a spike plan to validate design assumptions
- CritiqueDocuments: Evaluate documents for quality
- RefineDocuments: Apply targeted edits based on critique feedback
"""

from .critique_documents import CritiqueDocuments
from .draft_spike_plan import DraftSpikePlan
from .extract_principles import ExtractPrinciples
from .refine_documents import RefineDocuments

__all__ = [
    "CritiqueDocuments",
    "DraftSpikePlan",
    "ExtractPrinciples",
    "RefineDocuments",
]
