"""Shared runtime wiring for public and private SENEX apps."""
from __future__ import annotations

from .audit_store import AuditStore
from .data_retriever import DataRetriever
from .event_bus import EventBus
from .execution_simulator import ExecutionSimulator
from .oracle_engine import OracleEngine
from .scanner_a import ScannerA
from .scanner_b import ScannerB
from .scheduler import Scheduler
from .wallet_tracker import WalletTracker
from . import oracle_runner

_audit = AuditStore(root="data/audit")
_bus = EventBus(audit=_audit)
_retriever = DataRetriever(mode="LIVE", seed=42)
_scanner_a = ScannerA()
_scanner_b = ScannerB()
_wallet_tracker = WalletTracker()
_engine = OracleEngine()
_executor = ExecutionSimulator()
_scheduler = Scheduler(
    bus=_bus,
    retriever=_retriever,
    scanner_a=_scanner_a,
    scanner_b=_scanner_b,
    wallet_tracker=_wallet_tracker,
    engine=_engine,
    executor=_executor,
)


def _get_coordinator():
    try:
        return oracle_runner._get_portfolio_coordinator()
    except Exception:
        return None


def _paper_locked_live_gate_from_score(coord, score: dict) -> dict:
    status = coord.evaluate_live_gate(oracle_score=score)
    status.unlocked = False
    status.trade_mode = "PAPER"
    status.live_capital_locked = True
    policy_reason = "LIVE_CAPITAL_LOCKED_BY_PAPER_POLICY"
    if policy_reason not in status.failed_reasons:
        status.failed_reasons.append(policy_reason)

    state = status.to_dict()
    conditions = state.get("conditions") or {}
    state.update({
        "diagnostic_only": True,
        "effective_gate": "LOCKED_BY_PAPER_POLICY",
        "paper_only": True,
        "score_scope": score.get("score_scope"),
        "requested_symbol": score.get("requested_symbol"),
        "authority_cohort": score.get("authority_cohort"),
        "authority_n_source": (score.get("authority_1h") or {}).get("n_source"),
        "verified": int(score.get("independent_1h_rows") or 0),
        "proof_qualified_rows_raw": int(score.get("proof_qualified_rows_raw") or 0),
        "conditions_passed": sum(
            1
            for item in conditions.values()
            if isinstance(item, dict) and item.get("pass")
        ),
        "conditions_total": len(conditions),
    })
    return state
