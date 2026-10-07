[Reading 829 lines from start (total: 829 lines, 0 remaining)]

"""
SENECIO ORACLE — ACT XXV: Portfolio Coordinator
================================================

Wires the 6 ACT-XXV modules together into a single pipeline:

    Oracle Prediction (DO NOT TOUCH)
           │
           ▼
    PortfolioEngine.build_proposal()  ──► TradeProposal
           │
           ▼
    RiskKernel.evaluate()              ──► RiskDecision
           │
           ▼ (if approved)
    ExecutionEngine.submit()           ──► Order → Fills → Position
           │
           ▼
    TradeJournal.on_audit_event()      ──► JSONL record per trade
           │
           ▼
    ShadowLive.on_audit_event()        ──► paired real-book snapshot
           │
           ▼
    PortfolioAnalytics.compute()       ──► Sharpe/Sortino/PF/etc.
           │
           ▼
    LiveGate.evaluate()                ──► PAPER (locked) | LIVE (unlocked)

This coordinator sits OUTSIDE the existing oracle_runner — it consumes
the prediction dicts that oracle_runner already produces (without
modifying them) and routes them through the institutional pipeline.

The verifier (oracle_runner._verify_pending_outcomes) and prediction
model (predict_only.run_prediction) are NOT TOUCHED. The coordinator
only listens for new predictions via the `ingest_prediction()` method,
which oracle_runner calls after persisting each prediction.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .portfolio_engine import PortfolioEngine, PortfolioState
from .risk_kernel import RiskKernel
from .execution_engine import ExecutionEngine
from .trade_journal import TradeJournal
from .portfolio_analytics import PortfolioAnalytics
from .shadow_live import ShadowLive
from .live_gate import LiveGate, GateStatus
# ACT-XXVI additions
from .microstructure import MicrostructureIntelligence, MicrostructureReport
from .meta_labeler import MetaLabeler, MetaLabel
from .regime_hmm import HMMRegimeOverlay, RegimeBelief
from .execution_fidelity import BookSnapshot, book_snapshot_from_dict
from .paper_control_state import (
    STATE_VERSION as PAPER_CONTROL_STATE_VERSION,
    PaperControlStateStore,
    bootstrap_from_closed_journal,
    journal_has_nonblank_content,
    kernel_state_from_state,
    load_strict_closed_journal,
    position_from_state,
)

log = logging.getLogger("senecio.portfolio_coordinator")


class PortfolioCoordinator:
    """Orchestrates the 6 ACT-XXV modules.

    Lifecycle:
      1. coordinator = PortfolioCoordinator()
      2. coordinator.start()   — initializes all sub-modules + audit listeners
      3. coordinator.ingest_prediction(prediction_dict, last_price=..., vol_pct=...)
         — called by oracle_runner after each new prediction is persisted
      4. coordinator.on_tick(symbol, price, ts)
         — called by the scheduler on every market tick; checks exits
      5. coordinator.stop()    — generates final reports
    """

    def __init__(
        self,
        portfolio_engine: Optional[PortfolioEngine] = None,
        risk_kernel: Optional[RiskKernel] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        trade_journal: Optional[TradeJournal] = None,
        portfolio_analytics: Optional[PortfolioAnalytics] = None,
        shadow_live: Optional[ShadowLive] = None,
        live_gate: Optional[LiveGate] = None,
        # ACT-XXVI additions
        microstructure: Optional[MicrostructureIntelligence] = None,
        meta_labeler: Optional[MetaLabeler] = None,
        regime_hmm: Optional[HMMRegimeOverlay] = None,
        config: Optional[dict] = None,
    ):
        self.cfg = config or {}
        self.portfolio_engine = portfolio_engine or PortfolioEngine(config=self.cfg)
        self.risk_kernel = risk_kernel or RiskKernel(config=self.cfg)
        self.execution_engine = execution_engine or ExecutionEngine(config=self.cfg)
        self.trade_journal = trade_journal or TradeJournal(
            path=self.cfg.get("journal_path"),
            supabase_mirror=self.cfg.get("supabase_mirror", False),
        )
        control_state_path = self.cfg.get("paper_control_state_path")
        if control_state_path is None and self.cfg.get("journal_path"):
            control_state_path = str(
                Path(str(self.cfg["journal_path"])).with_name("paper_control_state.json")
            )
        self._control_state_store = PaperControlStateStore(path=control_state_path)
        raw_bootstrap = self.cfg.get("paper_control_allow_journal_bootstrap")
        if raw_bootstrap is None:
            raw_bootstrap = os.environ.get("SENEX_PAPER_CONTROL_ALLOW_JOURNAL_BOOTSTRAP")
        self._allow_journal_bootstrap = str(raw_bootstrap or "").strip().lower() in {
            "1", "true", "yes", "on"
        }
        self._control_state_restored = False
        self._control_state_migration = "NONE"
        self._control_order_count_semantics = "EXACT"
        self._decision_state_migration = {
            "meta_labeler": "NONE",
            "microstructure": "NONE",
        }
        self.portfolio_analytics = portfolio_analytics or PortfolioAnalytics(config=self.cfg)
        self.shadow_live = shadow_live or ShadowLive(config=self.cfg)
        self.live_gate = live_gate or LiveGate()
        # ACT-XXVI modules (lazily constructed if not provided)
        self.microstructure = microstructure or MicrostructureIntelligence(config=self.cfg)
        self.meta_labeler = meta_labeler or MetaLabeler(config=self.cfg)
        self.regime_hmm = regime_hmm or HMMRegimeOverlay(config=self.cfg)
        # Inject ACT-XXVI observers into the existing modules (additive —
        # they stay None-safe in the existing modules so this is non-breaking).
        self.portfolio_engine.meta_labeler = self.meta_labeler
        self.risk_kernel.microstructure = self.microstructure
        # Last-seen caches (for API exposure)
        self._last_microstructure_report: Optional[MicrostructureReport] = None
        self._last_meta_label: Optional[MetaLabel] = None
        self._last_regime_belief: Optional[RegimeBelief] = None

        # State cache
        self._portfolio_state: PortfolioState = PortfolioState(
            equity=self.cfg.get("starting_equity_usd", 10_000.0),
            cash=self.cfg.get("starting_equity_usd", 10_000.0),
        )
        self._last_prices: dict[str, float] = {}
        self._gate_status: Optional[GateStatus] = None
        self._started = False

    # -------- lifecycle --------

    def start(self) -> None:
        """Restore durable PAPER state, then wire listeners and start."""
        if self._started:
            return
        self._restore_or_initialize_control_state()
        # TradeJournal + ShadowLive both listen to ExecutionEngine's audit stream.
        self.execution_engine.set_audit_listener(self._on_audit_event)
        self._started = True
        self._persist_control_state()
        log.info(
            "PortfolioCoordinator started — equity=$%.2f trade_mode=%s live_locked=%s restored=%s migration=%s",
            self._portfolio_state.equity,
            self.cfg.get("trade_mode", "PAPER"),
            self.cfg.get("live_capital_locked", True),
            self._control_state_restored,
            self._control_state_migration,
        )

    async def stop(self) -> dict[str, Any]:
        """Generate final reports and stop shadow mode."""
        if not self._started:
            return {}
        self._persist_control_state()
        report = self.shadow_live.stop()
        self._started = False
        log.info("PortfolioCoordinator stopped")
        return report

    # -------- public API (called by oracle_runner / scheduler) --------

    async def ingest_prediction(
        self,
        prediction: dict[str, Any],
        last_price: Optional[float] = None,
        vol_pct: Optional[float] = None,
        win_rate_by_direction: Optional[dict[str, float]] = None,
        economic_edge_by_direction: Optional[dict[str, dict[str, Any]]] = None,
        book_depth_usd: Optional[float] = None,
        # ACT-XXVI additions
        ohlcv: Optional[list[list]] = None,
        orderbook: Optional[dict] = None,
        funding_rate: Optional[float] = None,
        oi_change_24h_pct: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        """Process a new oracle prediction through the full ACT-XXV/XXVI pipeline.

        Called by oracle_runner after each prediction is persisted.

        Returns a dict with the proposal + decision + order info (or None
        if the prediction was FLAT or skipped).

        ACT-XXVI additions:
          - ohlcv + funding + OI are fed into MicrostructureIntelligence
            (VPIN + OFI + liquidation + funding/OI) BEFORE risk evaluation
            so the RiskKernel can REJECT/REDUCE based on toxic flow.
          - ohlcv also feeds HMMRegimeOverlay — the belief is cached and
            exposed via /api/portfolio/regime_hmm (additive, no behavior
            change to the prediction model).
          - If `orderbook` is provided (with bids/asks lists), it's
            converted to a BookSnapshot and passed to the ExecutionEngine
            for the high-fidelity L2-walk fill path.
        """
        if not self._started:
            log.warning("coordinator not started — call start() first")
            return None

        symbol = prediction.get("symbol") or ""
        direction = (prediction.get("prediction") or "").upper()
        if direction not in ("LONG", "SHORT"):
            return None

        # Use the prediction's price_now as the reference price if no last_price
        ref_price = last_price or float(prediction.get("price_now") or 0)
        if ref_price <= 0:
            log.warning("ingest_prediction: no valid price for %s", symbol)
            return None

        # Update last-price cache
        self._last_prices[symbol] = ref_price

        # ACT-XXVI Step 0: feed microstructure + HMM observers BEFORE proposal build.
        # This ensures the RiskKernel sees a fresh toxic-flow report and the
        # MetaLabeler sees a fresh regime belief when they're consulted.
        toxic_score = 0.0
        if ohlcv:
            try:
                self.microstructure.ingest_ohlcv(ohlcv)
            except Exception as e:
                log.debug("microstructure ohlcv ingest failed: %s", e)
            try:
                self._last_regime_belief = self.regime_hmm.update_from_ohlcv(
                    ohlcv=ohlcv,
                    funding_rate=funding_rate,
                    oi_change_pct=oi_change_24h_pct,
                )
            except Exception as e:
                log.debug("regime_hmm update failed: %s", e)
        if funding_rate is not None and oi_change_24h_pct is not None:
            try:
                self.microstructure.ingest_funding_oi(funding_rate, oi_change_24h_pct)
            except Exception as e:
                log.debug("microstructure funding/oi ingest failed: %s", e)
        if orderbook:
            # Ingest top-of-book sizes into OFI estimator
            try:
                bids = orderbook.get("bids") or []
                asks = orderbook.get("asks") or []
                if bids and asks:
                    # bids/asks may be [[price, size], ...] or [{"price":..,"size":..}]
                    def _first_size(levels):
                        if not levels:
                            return 0.0
                        lvl = levels[0]
                        if isinstance(lvl, dict):
                            return float(lvl.get("size") or lvl.get("qty") or 0)
                        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                            return float(lvl[1])
                        return 0.0
                    self.microstructure.ingest_top_of_book(
                        bid_size=_first_size(bids),
                        ask_size=_first_size(asks),
                    )
            except Exception as e:
                log.debug("microstructure top-of-book ingest failed: %s", e)
        # Compute toxic-flow report (cached for API + passed to FillSimulator)
        try:
            self._last_microstructure_report = self.microstructure.evaluate(
                current_price=ref_price,
                direction=direction,
            )
            toxic_score = self._last_microstructure_report.toxic_score
        except Exception as e:
            log.debug("microstructure evaluate failed: %s", e)

        # Keep RiskKernel volatility regime synchronized with the same observed
        # volatility input used for proposal sizing.
        if vol_pct is not None:
            self.risk_kernel.update_vol_regime(float(vol_pct))

        # Refresh portfolio state from ExecutionEngine's open positions
        self._refresh_portfolio_state()

        # 1) Build proposal (PortfolioEngine consults MetaLabeler if attached)
        proposal = self.portfolio_engine.build_proposal(
            prediction=prediction,
            state=self._portfolio_state,
            vol_pct=vol_pct,
            win_rate_by_direction=win_rate_by_direction,
            economic_edge_by_direction=economic_edge_by_direction,
        )
        if proposal is None:
            self._persist_control_state()
            return {"skipped": "no_proposal", "prediction_id": prediction.get("id")}

        # 2) Risk-gate evaluation (RiskKernel consults MicrostructureIntelligence)
        decision = self.risk_kernel.evaluate(proposal)
        if not decision.approved:
            self._persist_control_state()
            return {
                "skipped": "risk_rejected",
                "reason": decision.reason,
                "prediction_id": prediction.get("id"),
                "microstructure": (
                    self._last_microstructure_report.to_dict()
                    if self._last_microstructure_report else None
                ),
            }

        # 3) Submit to ExecutionEngine (paper mode)
        # ACT-XXVI: pass BookSnapshot for high-fidelity L2 fill if available
        book_snapshot = None
        if orderbook:
            try:
                enriched_book = dict(orderbook)
                enriched_book.setdefault("symbol", symbol)
                enriched_book.setdefault("last_price", ref_price)
                enriched_book.setdefault("toxic_flow_score", toxic_score)
                book_snapshot = book_snapshot_from_dict(enriched_book)
            except Exception as e:
                log.debug("book_snapshot_from_dict failed: %s", e)
        order = await self.execution_engine.submit(
            proposal=proposal,
            decision=decision,
            last_price=ref_price,
            book_depth_usd=book_depth_usd,
            book_snapshot=book_snapshot,
            toxic_flow_score=toxic_score,
        )

        # 4) Any executed quantity creates exposure, including partial fills
        # whose residual is canceled. Protect every non-zero filled position.
        if order.filled_qty > 0:
            self.execution_engine.set_stop_target(
                symbol=symbol,
                stop_price=proposal.stop_price,
                target_price=proposal.target_price,
            )

        # Even an approved proposal that ends with no durable position changes
        # risk counters and rolling decision state. Checkpoint it explicitly.
        self._persist_control_state()

        return {
            "proposal": proposal.to_dict(),
            "decision": decision.to_dict(),
            "order": order.to_dict(),
            "prediction_id": prediction.get("id"),
            # ACT-XXVI enrichment
            "microstructure": (
                self._last_microstructure_report.to_dict()
                if self._last_microstructure_report else None
            ),
            "regime_hmm": (
                self._last_regime_belief.to_dict()
                if self._last_regime_belief else None
            ),
            "fidelity_model": "l2_walk" if book_snapshot else "legacy_stochastic",
        }

    def on_tick(
        self,
        symbol: str,
        price: float,
        ts: Optional[str] = None,
    ) -> list[dict]:
        """Check open positions against a new market tick.

        Called by the scheduler on every market tick. Returns a list of
        exit-event dicts (empty if no positions exited).
        """
        if not self._started:
            return []
        self._last_prices[symbol] = price
        ts_iso = ts or datetime.now(timezone.utc).isoformat()
        kill_switch = self.risk_kernel.state.kill_switch_active
        exits = self.execution_engine.check_exits(
            symbol=symbol,
            tick_price=price,
            tick_ts=ts_iso,
            kill_switch_active=kill_switch,
        )
        # Update RiskKernel with realized PnL from each exit
        # ACT-XXVI: also feed the outcome into MetaLabeler for streak tracking
        for exit_evt in exits:
            pnl = float(exit_evt.get("realized_pnl") or 0)
            equity = self.execution_engine.equity(self._last_prices)
            self.risk_kernel.record_pnl(pnl_usd=pnl, equity=equity)
            # Record outcome for meta-labeler (uses position's direction)
            try:
                pos = exit_evt.get("position") or {}
                direction = (pos.get("direction") or "").upper()
                result = "WIN" if pnl > 0 else "LOSS"
                if direction in ("LONG", "SHORT"):
                    self.meta_labeler.record_outcome(direction, result)
            except Exception as e:
                log.debug("meta_labeler record_outcome failed: %s", e)
        self._persist_control_state()
        return exits

    def evaluate_live_gate(
        self,
        oracle_score: Optional[dict] = None,
    ) -> GateStatus:
        """Evaluate the 6 LIVE_GATE conditions.

        Pulls analytics + shadow + exec self-test from internal modules.
        """
        trades = self.trade_journal.fetch_all()
        analytics_report = self.portfolio_analytics.compute(trades)
        shadow_report = self.shadow_live.generate_report()
        exec_self_test = self._exec_self_test()
        status = self.live_gate.evaluate(
            oracle_score=oracle_score,
            analytics_report=analytics_report,
            shadow_report=shadow_report,
            exec_self_test=exec_self_test,
        )
        self._gate_status = status
        return status

    # -------- introspection --------

    def get_state(self) -> dict[str, Any]:
        """Snapshot of the entire portfolio subsystem."""
        self._refresh_portfolio_state()
        return {
            "version": "ACT-XXVI-deep-edge-integration",
            "started": self._started,
            "portfolio_state": self._portfolio_state.to_dict(),
            "risk_kernel": self.risk_kernel.get_state(),
            "execution_engine": self.execution_engine.stats(),
            "trade_journal": self.trade_journal.stats(),
            "shadow_live": self.shadow_live.stats(),
            "live_gate": self._gate_status.to_dict() if self._gate_status else None,
            "last_prices": self._last_prices,
            # ACT-XXVI additions
            "microstructure": self.microstructure.stats(),
            "meta_labeler": self.meta_labeler.stats(),
            "regime_hmm": self.regime_hmm.stats(),
            "last_microstructure_report": (
                self._last_microstructure_report.to_dict()
                if self._last_microstructure_report else None
            ),
            "last_regime_belief": (
                self._last_regime_belief.to_dict()
                if self._last_regime_belief else None
            ),
            "paper_control_persistence": {
                "version": PAPER_CONTROL_STATE_VERSION,
                "state_path_name": self._control_state_store.path.name,
                "state_exists": self._control_state_store.exists(),
                "restored": self._control_state_restored,
                "migration": self._control_state_migration,
                "order_count_semantics": self._control_order_count_semantics,
                "decision_state_migration": dict(self._decision_state_migration),
            },
        }

    def get_microstructure_report(self) -> dict[str, Any]:
        """Return the most recent MicrostructureReport (or live evaluate)."""
        if self._last_microstructure_report is not None:
            return self._last_microstructure_report.to_dict()
        # Fallback: build a snapshot from the observer
        return self.microstructure.stats()

    def get_regime_belief(self) -> dict[str, Any]:
        """Return the most recent HMM RegimeBelief."""
        if self._last_regime_belief is not None:
            return self._last_regime_belief.to_dict()
        return self.regime_hmm.snapshot().to_dict()

    def get_analytics(self) -> dict[str, Any]:
        """Compute the full PortfolioAnalytics report."""
        trades = self.trade_journal.fetch_all()
        return self.portfolio_analytics.compute(trades)

    def get_shadow_report(self) -> dict[str, Any]:
        """Get (or generate) the ShadowLive aggregate report."""
        return self.shadow_live.generate_report()

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        return self.trade_journal.fetch_recent(limit=limit)

    def get_audit_log(self, limit: int = 50) -> list[dict]:
        return self.execution_engine.get_audit_log(limit=limit)

    # -------- kill switch (manual) --------

    def trip_kill_switch(self, reason: str) -> None:
        """Manually trip the kill switch — halts all new trades."""
        self.risk_kernel.trip_kill_switch(reason)
        self._persist_control_state()

    def reset_kill_switch(self, reason: str = "manual reset") -> None:
        """Clear the kill switch (requires explicit human action)."""
        self.risk_kernel.reset_kill_switch(reason)
        self._persist_control_state()

    # -------- durable PAPER control state --------

    def _control_state_payload(self) -> dict[str, Any]:
        return {
            "version": PAPER_CONTROL_STATE_VERSION,
            "paper_only": True,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "migration": self._control_state_migration,
            "execution": {
                "cash": float(self.execution_engine.cash),
                "starting_cash": float(self.execution_engine.starting_cash),
                "total_orders": int(self.execution_engine.stats()["total_orders"]),
                "order_count_semantics": self._control_order_count_semantics,
                "closed_position_count_offset": int(
                    self.execution_engine._closed_position_count_offset
                ),
                "historical_closed_trade_ids": list(
                    self.execution_engine._historical_closed_trade_ids
                ),
                "open_positions": [
                    pos.to_dict() for pos in self.execution_engine.positions.values()
                    if pos.status == "OPEN"
                ],
                "closed_positions": [
                    pos.to_dict() for pos in self.execution_engine.closed_positions
                ],
            },
            "risk_state": self.risk_kernel.state.to_dict(),
            "last_prices": {
                str(symbol): float(price)
                for symbol, price in self._last_prices.items()
            },
            "meta_labeler_state": self.meta_labeler.persistent_state(),
            "microstructure_state": self.microstructure.persistent_state(),
            "decision_state_migration": getattr(
                self,
                "_decision_state_migration",
                {
                    "meta_labeler": "NATIVE",
                    "microstructure": "NATIVE",
                },
            ),
        }

    def _persist_control_state(self) -> None:
        if self.execution_engine.cfg.get("trade_mode", "PAPER") != "PAPER":
            raise RuntimeError("PAPER_CONTROL_STATE_REFUSES_NON_PAPER_MODE")
        if self.execution_engine.cfg.get("allow_live", False):
            raise RuntimeError("PAPER_CONTROL_STATE_REFUSES_LIVE_CAPABILITY")
        payload = self._control_state_payload()
        self._assert_journal_state_consistency(payload)
        self._control_state_store.save(payload)

    def _assert_journal_state_consistency(self, payload: dict[str, Any]) -> None:
        """Fail closed if the state file and durable closed-trade journal disagree."""
        rows = load_strict_closed_journal(self.trade_journal.path)
        journal_by_id: dict[str, dict[str, Any]] = {}
        for row in rows:
            trade_id = str(row.get("trade_id") or "")
            if not trade_id or trade_id in journal_by_id:
                raise RuntimeError(
                    "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:JOURNAL_IDENTITY"
                )
            journal_by_id[trade_id] = row

        execution = payload.get("execution") or {}
        open_rows = execution.get("open_positions") or []
        closed_rows = execution.get("closed_positions") or []
        open_ids = {
            str(row.get("position_id") or "")
            for row in open_rows
            if isinstance(row, dict)
        }
        closed_by_id: dict[str, dict[str, Any]] = {}
        for row in closed_rows:
            if not isinstance(row, dict):
                raise RuntimeError(
                    "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:CLOSED_POSITION"
                )
            position_id = str(row.get("position_id") or "")
            if not position_id or position_id in closed_by_id:
                raise RuntimeError(
                    "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:CLOSED_IDENTITY"
                )
            closed_by_id[position_id] = row

        historical_ids = execution.get("historical_closed_trade_ids") or []
        historical_id_set = set(historical_ids)
        if historical_id_set.intersection(closed_by_id):
            raise RuntimeError(
                "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:DUPLICATE_CLOSED_ID"
            )
        if open_ids.intersection(journal_by_id):
            raise RuntimeError(
                "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:OPEN_ALREADY_CLOSED"
            )
        represented_closed_ids = historical_id_set.union(closed_by_id)
        if represented_closed_ids != set(journal_by_id):
            raise RuntimeError(
                "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:CLOSED_ID_SET"
            )

        for position_id, position in closed_by_id.items():
            journal = journal_by_id[position_id]
            if str(position.get("symbol") or "") != str(journal.get("symbol") or ""):
                raise RuntimeError(
                    "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:SYMBOL"
                )
            if str(position.get("direction") or "").upper() != str(
                journal.get("direction") or ""
            ).upper():
                raise RuntimeError(
                    "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:DIRECTION"
                )
            state_pnl = float(position.get("realized_pnl") or 0.0)
            journal_pnl = float(journal.get("realized_pnl_usd") or 0.0)
            if abs(state_pnl - journal_pnl) > 1e-9:
                raise RuntimeError(
                    "PAPER_CONTROL_STATE_JOURNAL_MISMATCH:REALIZED_PNL"
                )

    def _restore_or_initialize_control_state(self) -> None:
        if self._control_state_store.exists():
            payload = self._control_state_store.load()
            self._assert_journal_state_consistency(payload)
            self._apply_control_state(payload)
            self._control_state_restored = True
            if self._control_state_migration == "NONE":
                self._control_state_migration = "RESTORED"
            return

        if journal_has_nonblank_content(self.trade_journal.path):
            if not self._allow_journal_bootstrap:
                raise RuntimeError(
                    "PAPER_CONTROL_STATE_MISSING_WITH_DURABLE_JOURNAL"
                )
            rows = load_strict_closed_journal(self.trade_journal.path)
            payload = bootstrap_from_closed_journal(
                rows,
                starting_cash=float(self.execution_engine.starting_cash),
                consecutive_loss_threshold=int(
                    self.risk_kernel.cfg["consecutive_loss_threshold"]
                ),
                cooldown_minutes=int(self.risk_kernel.cfg["cooldown_minutes"]),
                max_daily_loss_pct=float(self.risk_kernel.cfg["max_daily_loss_pct"]),
                max_drawdown_pct=float(self.risk_kernel.cfg["max_drawdown_pct"]),
            )
            # MetaLabeler can be reconstructed from the durable closed-trade
            # sequence because its decision state depends only on outcomes.
            for row in rows:
                direction = str(row.get("direction") or "").upper()
                if direction not in ("LONG", "SHORT"):
                    continue
                pnl = float(row.get("realized_pnl_usd") or 0.0)
                self.meta_labeler.record_outcome(
                    direction,
                    "WIN" if pnl > 0 else "LOSS",
                )
            payload["meta_labeler_state"] = self.meta_labeler.persistent_state()
            # Legacy deployments did not persist VPIN/OFI/liquidation history.
            # Do not invent it. Establish an explicit one-time reset boundary.
            payload["microstructure_state"] = self.microstructure.persistent_state()
            payload["decision_state_migration"] = {
                "meta_labeler": "RECONSTRUCTED_FROM_JOURNAL",
                "microstructure": "RESET_AT_LEGACY_BOOTSTRAP",
            }
            self._assert_journal_state_consistency(payload)
            self._control_state_store.save(payload)
            self._apply_control_state(payload)
            self._control_state_restored = True
            self._control_state_migration = "JOURNAL_BOOTSTRAP_EXPLICIT"
            return

        self._control_state_restored = False
        self._control_state_migration = "FRESH"
        self._decision_state_migration = {
            "meta_labeler": "FRESH",
            "microstructure": "FRESH",
        }

    def _apply_control_state(self, payload: dict[str, Any]) -> None:
        execution = payload["execution"]
        self.execution_engine.cash = float(execution["cash"])
        self.execution_engine.starting_cash = float(execution["starting_cash"])
        self.execution_engine.orders.clear()
        self.execution_engine._order_count_offset = int(
            execution.get("total_orders", 0)
        )
        self.execution_engine._closed_position_count_offset = int(
            execution.get("closed_position_count_offset", 0)
        )
        self.execution_engine._historical_closed_trade_ids = list(
            execution.get("historical_closed_trade_ids") or []
        )

        restored_open = {}
        for item in execution.get("open_positions", []):
            pos = position_from_state(item)
            if pos.status != "OPEN":
                raise RuntimeError("PAPER_CONTROL_STATE_CORRUPT:OPEN_POSITION_STATUS")
            if pos.symbol in restored_open:
                raise RuntimeError("PAPER_CONTROL_STATE_CORRUPT:DUPLICATE_OPEN_SYMBOL")
            restored_open[pos.symbol] = pos
        self.execution_engine.positions = restored_open
        self.execution_engine.closed_positions = [
            position_from_state(item)
            for item in execution.get("closed_positions", [])
        ]
        self.risk_kernel.state = kernel_state_from_state(payload["risk_state"])
        self._last_prices = {
            str(symbol): float(price)
            for symbol, price in payload.get("last_prices", {}).items()
        }
        try:
            self.meta_labeler.restore_persistent_state(
                payload["meta_labeler_state"]
            )
        except Exception as exc:
            raise RuntimeError(
                "PAPER_CONTROL_STATE_CORRUPT:META_LABELER_STATE"
            ) from exc
        try:
            self.microstructure.restore_persistent_state(
                payload["microstructure_state"]
            )
        except Exception as exc:
            raise RuntimeError(
                "PAPER_CONTROL_STATE_CORRUPT:MICROSTRUCTURE_STATE"
            ) from exc
        decision_migration = payload.get("decision_state_migration")
        if not isinstance(decision_migration, dict):
            raise RuntimeError(
                "PAPER_CONTROL_STATE_CORRUPT:DECISION_STATE_MIGRATION"
            )
        self._decision_state_migration = {
            "meta_labeler": str(
                decision_migration.get("meta_labeler") or "RESTORED"
            ),
            "microstructure": str(
                decision_migration.get("microstructure") or "RESTORED"
            ),
        }
        self._control_state_migration = str(payload.get("migration") or "RESTORED")
        self._control_order_count_semantics = str(
            execution.get("order_count_semantics") or "EXACT"
        )

        # TradeJournal pending state is in-memory only. Re-seed it for any
        # restored open position so the eventual exit remains a complete row.
        for pos in restored_open.values():
            self.trade_journal.on_audit_event(
                {"event": "POSITION_OPEN", "position": pos.to_dict()}
            )
        self._refresh_portfolio_state()

    # -------- internal helpers --------

    def _refresh_portfolio_state(self) -> None:
        """Sync _portfolio_state with ExecutionEngine's open positions."""
        open_positions: dict[str, dict] = {}
        for sym, pos in self.execution_engine.positions.items():
            if pos.status == "OPEN":
                open_positions[sym] = pos.to_dict()
        self._portfolio_state = self.portfolio_engine.recompute_state(
            open_positions=open_positions,
            cash=self.execution_engine.cash,
            starting_equity=self.cfg.get("starting_equity_usd", 10_000.0),
            last_prices=self._last_prices,
        )

    def _on_audit_event(self, event: dict) -> None:
        """Fan-out an ExecutionEngine audit event and checkpoint PAPER state."""
        self.trade_journal.on_audit_event(event)
        self.shadow_live.on_audit_event(event)
        # Persist only stable execution transitions. FILL / ORDER_FILLED /
        # PARTIAL_FILL are intermediate states; POSITION_EXIT is emitted before
        # ExecutionEngine moves the position from open -> closed, so on_tick()
        # checkpoints that completed transition instead.
        if event.get("event") in {
            "POSITION_OPEN",
            "POSITION_STOP_TARGET_SET",
            "ORDER_REJECTED",
        }:
            self._persist_control_state()

    def _exec_self_test(self) -> dict[str, Any]:
        """Basic ExecutionEngine self-test for LIVE_GATE condition #6."""
        try:
            # 1) Can we create an Order?
            from .execution_engine import Order, OrderStatus
            test_order = Order(
                order_id="selftest",
                client_order_id="selftest-cid",
                symbol="SELFTEST/USDT",
                side="BUY",
                direction="LONG",
                ordered_qty=1.0,
                status=OrderStatus.NEW.value,
            )
            # 2) Is allow_live properly locked?
            allow_live = self.execution_engine.cfg.get("allow_live", False)
            # 3) Is trade_mode PAPER?
            trade_mode = self.execution_engine.cfg.get("trade_mode", "PAPER")
            verified = (
                test_order.order_id == "selftest"
                and not allow_live
                and trade_mode == "PAPER"
            )
            return {
                "verified": verified,
                "allow_live": allow_live,
                "trade_mode": trade_mode,
                "audit_log_size": len(self.execution_engine.audit_log),
                "open_positions": len(self.execution_engine.positions),
            }
        except Exception as e:
            log.exception("exec self-test failed: %s", e)
            return {"verified": False, "error": str(e)}

[executed on device: DESKTOP-DPH3941 (f5db7315-cdea-42b4-b067-243411e4a115)]