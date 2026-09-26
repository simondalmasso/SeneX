"""GPTrader PAPER-only subsystem foundations.

P0 contains only immutable T0 packet sealing and cursor primitives. It has no
broker, wallet, signer, LIVE route, D1 dependency, or decision execution path.
"""

from .cursor import CursorError, PacketCursor
from .paths import GPTraderPaths
from .sealer import (
    OutcomeContaminationError,
    PacketSealer,
    PacketSequenceError,
    PacketSizeError,
    build_sealed_packet,
    canonical_json,
    seal_prediction_t0,
)
from .baselines import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    RAW_SCORE_SEMANTICS,
    BaselinePolicy,
    PolicyDecision,
    evaluate_baseline,
)
from .science import (
    MIN_CALENDAR_DAYS,
    MIN_INDEPENDENT_1H,
    ResolvedSampleSummary,
    SampleGate,
    hour_cluster_id,
    sample_gate,
    summarize_resolved_sample,
)

__all__ = [
    "CursorError",
    "GPTraderPaths",
    "OutcomeContaminationError",
    "PacketCursor",
    "PacketSealer",
    "PacketSequenceError",
    "PacketSizeError",
    "build_sealed_packet",
    "canonical_json",
    "seal_prediction_t0",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "RAW_SCORE_SEMANTICS",
    "BaselinePolicy",
    "PolicyDecision",
    "evaluate_baseline",
    "MIN_CALENDAR_DAYS",
    "MIN_INDEPENDENT_1H",
    "ResolvedSampleSummary",
    "SampleGate",
    "hour_cluster_id",
    "sample_gate",
    "summarize_resolved_sample",
]
