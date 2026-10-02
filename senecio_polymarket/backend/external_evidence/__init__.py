"""SENEX ORDER095 external evidence shadow fabric.

This package is deliberately isolated from prediction and GPTrader decision paths.
Captured evidence is research-only until a separate prospective hypothesis promotes it.
"""

from .journal import AppendResult, ExternalEvidenceJournal
from .paths import ExternalEvidencePaths
from .schema import EvidenceCapture, ExternalEvidenceEvent, EvidenceValidationError
from .service import ShadowEvidenceService

__all__ = [
    "AppendResult",
    "EvidenceCapture",
    "EvidenceValidationError",
    "ExternalEvidenceEvent",
    "ExternalEvidenceJournal",
    "ExternalEvidencePaths",
    "ShadowEvidenceService",
]
