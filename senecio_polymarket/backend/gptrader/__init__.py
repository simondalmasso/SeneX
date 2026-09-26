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
    build_sealed_packet,
    canonical_json,
    seal_prediction_t0,
)

__all__ = [
    "CursorError",
    "GPTraderPaths",
    "OutcomeContaminationError",
    "PacketCursor",
    "PacketSealer",
    "PacketSequenceError",
    "build_sealed_packet",
    "canonical_json",
    "seal_prediction_t0",
]
