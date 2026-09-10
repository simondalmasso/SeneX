"""
SENECIO ORACLE — ACT XXVI: Portfolio subpackage
================================================

Institutional execution layer built on top of the existing oracle pipeline.
Does NOT touch:
  - prediction model (predict_only.py)
  - feature engineering (institutional_core.py compress_features)
  - signal generation (institutional_core.py produce_action)
  - verifier (oracle_runner.py _verify_pending_outcomes / _backfill_bogus_outcomes)

ACT-XXV (baseline): 6 modules + LIVE_GATE.
ACT-XXVI (deep edge integration): +3 modules —
  - ExecutionFidelity (HftBacktest-style L2 walk + queue + impact)
  - MicrostructureIntelligence (VPIN + OFI + liquidation + funding/OI)
  - MetaLabeler (triple-barrier LONG-side secondary filter)
  - HMMRegimeOverlay (probabilistic regime belief, augments regime_filter_4h)

Public API:
    from .portfolio.coordinator import PortfolioCoordinator
    coordinator = PortfolioCoordinator()
    coordinator.start()
    await coordinator.ingest_prediction(prediction_dict, last_price=...)
"""
from .portfolio_engine import PortfolioEngine, TradeProposal, PortfolioState
from .risk_kernel import RiskKernel, RiskDecision, KernelState, VolRegime
from .execution_engine import ExecutionEngine, Order, Fill, Position, OrderStatus, ExitReason
from .trade_journal import TradeJournal
from .portfolio_analytics import PortfolioAnalytics
from .shadow_live import ShadowLive, ShadowTrade
from .live_gate import LiveGate, GateStatus
from .coordinator import PortfolioCoordinator
# ACT-XXVI modules
from .execution_fidelity import (
    FillSimulator,
    BookSnapshot,
    BookLevel,
    FillEstimate,
    QueuePositionModel,
    walk_book,
    estimate_market_impact,
    book_snapshot_from_dict,
)
from .microstructure import (
    MicrostructureIntelligence,
    MicrostructureReport,
    VPINEstimator,
    OFIEstimator,
    LiquidationClusterDetector,
)
from .meta_labeler import (
    MetaLabeler,
    MetaLabel,
    TripleBarrier,
)
from .regime_hmm import (
    HMMRegimeOverlay,
    RegimeBelief,
)

__all__ = [
    # ACT-XXV baseline
    "PortfolioEngine",
    "TradeProposal",
    "PortfolioState",
    "RiskKernel",
    "RiskDecision",
    "KernelState",
    "VolRegime",
    "ExecutionEngine",
    "Order",
    "Fill",
    "Position",
    "OrderStatus",
    "ExitReason",
    "TradeJournal",
    "PortfolioAnalytics",
    "ShadowLive",
    "ShadowTrade",
    "LiveGate",
    "GateStatus",
    "PortfolioCoordinator",
    # ACT-XXVI additions
    "FillSimulator",
    "BookSnapshot",
    "BookLevel",
    "FillEstimate",
    "QueuePositionModel",
    "walk_book",
    "estimate_market_impact",
    "book_snapshot_from_dict",
    "MicrostructureIntelligence",
    "MicrostructureReport",
    "VPINEstimator",
    "OFIEstimator",
    "LiquidationClusterDetector",
    "MetaLabeler",
    "MetaLabel",
    "TripleBarrier",
    "HMMRegimeOverlay",
    "RegimeBelief",
]

VERSION = "ACT-XXVI-deep-edge-integration"
