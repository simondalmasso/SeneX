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
from datetime import datetime, timezone
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
            path=self.cfg.get("journal_path", "data/journal/trades.jsonl"),
            supabase_mirror=self.cfg.get("supabase_mirror", False),
        )
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
        """Wire audit listeners + initialize state."""
        if self._started:
            return
        # TradeJournal + ShadowLive both listen to ExecutionEngine's audit stream
        self.execution_engine.set_audit_listener(self._on_audit_event)
        self._started = True
        log.info(
            "PortfolioCoordinator started — equity=$%.2f trade_mode=%s live_locked=%s",
            self._portfolio_state.equity,
            self.cfg.get("trade_mode", "PAPER"),
            self.cfg.get("live_capital_locked", True),
        )

    async def stop(self) -> dict[str, Any]:
        """Generate final reports and stop shadow mode."""
        if not self._started:
            return {}
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

        # Refresh portfolio state from ExecutionEngine's open positions
        self._refresh_portfolio_state()

        # 1) Build proposal (PortfolioEngine consults MetaLabeler if attached)
        proposal = self.portfolio_engine.build_proposal(
            prediction=prediction,
            state=self._portfolio_state,
            vol_pct=vol_pct,
            win_rate_by_direction=win_rate_by_direction,
        )
        if proposal is None:
            return {"skipped": "no_proposal", "prediction_id": prediction.get("id")}

        # 2) Risk-gate evaluation (RiskKernel consults MicrostructureIntelligence)
        decision = self.risk_kernel.evaluate(proposal)
        if not decision.approved:
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

        # 4) If filled, attach stop/target from the proposal
        if order.status == "FILLED" and order.filled_qty > 0:
            self.execution_engine.set_stop_target(
                symbol=symbol,
                stop_price=proposal.stop_price,
                target_price=proposal.target_price,
            )

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

    def reset_kill_switch(self, reason: str = "manual reset") -> None:
        """Clear the kill switch (requires explicit human action)."""
        self.risk_kernel.reset_kill_switch(reason)

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
        """Fan-out an ExecutionEngine audit event to journal + shadow."""
        self.trade_journal.on_audit_event(event)
        self.shadow_live.on_audit_event(event)

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
