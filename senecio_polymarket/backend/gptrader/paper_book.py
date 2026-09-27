from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

from ..portfolio.execution_engine import ExecutionEngine, Position
from ..portfolio.portfolio_engine import TradeProposal
from ..portfolio.risk_kernel import KernelState, RiskKernel
from ..portfolio.trade_journal import TradeJournal
from .store import GPTraderStore

PRIMARY_STARTING_EQUITY = 10_000.0
FIXED_PRIMARY_RISK_PCT = 0.005
FIXED_STOP_PCT = 0.02
FIXED_TARGET_PCT = 0.04
OWNER_SCALE_ENV = "SENEX_GPTRADER_OWNER_SCALE_STARTING_EQUITY"


def _finite_positive(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return number


class GPTraderPaperBook:
    """Namespaced PAPER treatment using SENEX risk/execution primitives."""

    def __init__(
        self,
        store: GPTraderStore,
        *,
        owner_scale: bool = False,
    ):
        self.store = store
        self.owner_scale = bool(owner_scale)
        self.scale_label = "OWNER_SCALE_PRIVATE" if owner_scale else "PRIMARY_NORMALIZED"
        self.starting_equity = self._resolve_starting_equity()
        cfg = {
            "starting_equity_usd": self.starting_equity,
            "starting_cash": self.starting_equity,
            "trade_mode": "PAPER",
            "live_capital_locked": True,
            "allow_live": False,
        }
        self.risk_kernel = RiskKernel(config=cfg)
        self.execution_engine = ExecutionEngine(config=cfg)
        self.trade_journal = TradeJournal(
            path=str(self.store.paths.trades),
            supabase_mirror=False,
        )
        self.execution_engine.set_audit_listener(self.trade_journal.on_audit_event)
        self._last_prices: dict[str, float] = {}
        self._last_run_id: str | None = None
        self._applied_decisions: dict[str, dict[str, Any]] = {}
        self._restore()

    def _resolve_starting_equity(self) -> float:
        if not self.owner_scale:
            return PRIMARY_STARTING_EQUITY
        raw = os.environ.get(OWNER_SCALE_ENV)
        if raw is None:
            raise ValueError(f"{OWNER_SCALE_ENV} is required for owner-scale simulation")
        return _finite_positive(raw, OWNER_SCALE_ENV)

    def _restore(self) -> None:
        state = self.store.load_paper_state()
        durable_decisions = self.store.read_decisions()
        if not state:
            if durable_decisions:
                raise RuntimeError("PAPER_STATE_MISSING_WITH_DURABLE_DECISIONS")
            return
        if durable_decisions and "applied_decisions" not in state:
            raise RuntimeError("PAPER_STATE_APPLIED_LEDGER_MISSING")
        if state.get("scale_label") != self.scale_label:
            raise ValueError("paper state scale mismatch")
        self.execution_engine.cash = float(state.get("cash", self.starting_equity))
        self.execution_engine.starting_cash = float(
            state.get("starting_cash", self.starting_equity)
        )
        self.execution_engine.positions = {
            symbol: Position(**payload)
            for symbol, payload in (state.get("positions") or {}).items()
        }
        self.execution_engine.closed_positions = [
            Position(**payload) for payload in (state.get("closed_positions") or [])
        ]
        risk_state = state.get("risk_state")
        if isinstance(risk_state, dict):
            self.risk_kernel.state = KernelState(**risk_state)
        self._last_prices = {
            str(symbol): float(price)
            for symbol, price in (state.get("last_prices") or {}).items()
        }
        self._last_run_id = state.get("last_run_id")
        self._applied_decisions = {str(k): dict(v) for k, v in (state.get("applied_decisions") or {}).items() if isinstance(v, dict)}
        for position in self.execution_engine.positions.values():
            self.trade_journal.on_audit_event(
                {"event": "POSITION_OPEN", "position": position.to_dict()}
            )

    def _persist(self) -> None:
        self.store.save_paper_state(
            {
                "version": "gptrader.paper.v1",
                "scale_label": self.scale_label,
                "starting_cash": self.execution_engine.starting_cash,
                "cash": self.execution_engine.cash,
                "positions": {
                    symbol: position.to_dict()
                    for symbol, position in self.execution_engine.positions.items()
                },
                "closed_positions": [
                    position.to_dict()
                    for position in self.execution_engine.closed_positions
                ],
                "risk_state": self.risk_kernel.state.to_dict(),
                "last_prices": dict(self._last_prices),
                "last_run_id": self._last_run_id,
                "applied_decisions": dict(self._applied_decisions),
            }
        )

    @staticmethod
    def _validate_decision(packet: dict[str, Any], decision: dict[str, Any]) -> str:
        action = str(decision.get("action") or "").upper()
        if action not in {"TAKE", "ABSTAIN"}:
            raise ValueError("action must be TAKE or ABSTAIN")
        if any(key in decision for key in ("direction", "direction_override", "side", "flip")):
            raise ValueError("direction override is forbidden")
        if str(decision.get("packet_id") or "") != str(packet.get("packet_id") or ""):
            raise ValueError("decision packet_id mismatch")
        return action

    def _proposal(self, packet: dict[str, Any]) -> TradeProposal:
        direction = str(packet.get("prediction") or "").upper()
        if direction not in {"LONG", "SHORT"}:
            raise ValueError("TAKE requires directional SENEX packet")
        price = _finite_positive(packet.get("price_now"), "price_now")
        confidence = float(packet.get("confidence") or 0.0)
        ev = float(packet.get("ev") or 0.0)
        risk_usd = self.starting_equity * FIXED_PRIMARY_RISK_PCT
        risk_per_unit = price * FIXED_STOP_PCT
        size_qty = risk_usd / risk_per_unit
        if direction == "LONG":
            stop = price * (1.0 - FIXED_STOP_PCT)
            target = price * (1.0 + FIXED_TARGET_PCT)
        else:
            stop = price * (1.0 + FIXED_STOP_PCT)
            target = price * (1.0 - FIXED_TARGET_PCT)
        return TradeProposal(
            symbol=str(packet.get("symbol") or ""),
            direction=direction,
            size_usd=size_qty * price,
            size_qty=size_qty,
            entry_price=price,
            stop_price=stop,
            target_price=target,
            risk_per_unit=risk_per_unit,
            risk_usd=risk_usd,
            confidence=confidence,
            ev=ev,
            source="gptrader",
            prediction_id=str(packet.get("packet_id") or ""),
            rationale="GPTRADER_FIXED_PRIMARY_RISK",
        )

    async def apply_decision(
        self,
        packet: dict[str, Any],
        decision: dict[str, Any],
        *,
        run_id: str,
        persist_decision: bool = True,
    ) -> dict[str, Any]:
        action = self._validate_decision(packet, decision)
        packet_id = str(packet["packet_id"])
        policy_id = str(decision.get("policy_id") or "GPTRADER")
        exploratory_scale = decision.get("size_scale")
        apply_key = f"{policy_id}|{packet_id}"
        prior_result = self._applied_decisions.get(apply_key)
        if prior_result is not None:
            return dict(prior_result)
        self._last_run_id = str(run_id)

        if action == "ABSTAIN":
            result = {
                "classification": "ABSTAIN",
                "packet_id": packet_id,
                "policy_id": policy_id,
                "primary_risk_pct": FIXED_PRIMARY_RISK_PCT,
                "primary_size_scale": 1.0,
                "exploratory_size_scale": exploratory_scale,
            }
            if persist_decision:
                self._record_decision(run_id, packet, decision, result)
            self._applied_decisions[apply_key] = dict(result)
            self._persist()
            return result

        proposal = self._proposal(packet)
        risk = self.risk_kernel.evaluate(proposal)
        if not risk.approved:
            result = {
                "classification": "TAKE_REJECTED_BY_KERNEL",
                "packet_id": packet_id,
                "policy_id": policy_id,
                "reason": risk.reason,
                "primary_risk_pct": FIXED_PRIMARY_RISK_PCT,
                "primary_size_scale": risk.size_scale,
                "exploratory_size_scale": exploratory_scale,
            }
            if persist_decision:
                self._record_decision(run_id, packet, decision, result)
            self._applied_decisions[apply_key] = dict(result)
            self._persist()
            return result

        price = float(packet["price_now"])
        self._last_prices[proposal.symbol] = price
        order = await self.execution_engine.submit(
            proposal=proposal,
            decision=risk,
            last_price=price,
        )
        if order.filled_qty > 0:
            self.execution_engine.set_stop_target(
                symbol=proposal.symbol,
                stop_price=proposal.stop_price,
                target_price=proposal.target_price,
            )
        classification = "TAKE_ACCEPTED" if order.filled_qty > 0 else "TAKE_NO_FILL"
        result = {
            "classification": classification,
            "packet_id": packet_id,
            "policy_id": policy_id,
            "ordered_qty": order.ordered_qty,
            "filled_qty": round(order.filled_qty, 8),
            "avg_fill_price": round(order.avg_fill_price, 6),
            "order_status": order.status,
            "primary_risk_pct": FIXED_PRIMARY_RISK_PCT,
            "primary_size_scale": risk.size_scale,
            "exploratory_size_scale": exploratory_scale,
        }
        if persist_decision:
            self._record_decision(run_id, packet, decision, result)
        self._applied_decisions[apply_key] = dict(result)
        self._persist()
        return result

    def _record_decision(
        self,
        run_id: str,
        packet: dict[str, Any],
        decision: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "run_id": str(run_id),
            "policy_id": str(decision.get("policy_id") or "GPTRADER"),
            "packet_id": str(packet.get("packet_id") or ""),
            "action": str(decision.get("action") or "").upper(),
            "senex_direction": str(packet.get("prediction") or "").upper(),
            "classification": result["classification"],
            "primary_risk_pct": FIXED_PRIMARY_RISK_PCT,
            "primary_size_scale": result.get("primary_size_scale", 1.0),
            "exploratory_size_scale": result.get("exploratory_size_scale"),
            "reason_codes": list(decision.get("reason_codes") or []),
        }
        self.store.append_decision(row)
        self.store.append_run(
            {
                "ts": row["ts"],
                "run_id": row["run_id"],
                "event": "PAPER_DECISION_APPLIED",
                "packet_id": row["packet_id"],
                "classification": row["classification"],
            }
        )

    def state(self, *, public: bool = False) -> dict[str, Any]:
        equity = self.execution_engine.equity(self._last_prices)
        base = max(self.starting_equity, 1e-12)
        common = {
            "version": "gptrader.paper.v1",
            "scale_label": self.scale_label,
            "paper_only": True,
            "simulation_only": True,
            "live": False,
            "orders_enabled": False,
            "live_capital_locked": True,
            "fixed_primary_risk_pct": FIXED_PRIMARY_RISK_PCT,
            "open_count": len(self.execution_engine.positions),
            "closed_count": len(self.execution_engine.closed_positions),
            "journal_path": str(self.trade_journal.path),
            "last_run_id": self._last_run_id,
            "risk_state": self.risk_kernel.state.to_dict(),
        }
        if public and self.owner_scale:
            safe_risk = dict(common["risk_state"])
            for key in ("current_equity", "peak_equity", "daily_pnl_usd"):
                safe_risk.pop(key, None)
            return {
                **common,
                "risk_state": safe_risk,
                "normalized_cash_pct": round(
                    self.execution_engine.cash / base * 100.0, 6
                ),
                "normalized_equity_pct": round(equity / base * 100.0, 6),
            }
        return {
            **common,
            "cash": self.execution_engine.cash,
            "equity": equity,
        }
