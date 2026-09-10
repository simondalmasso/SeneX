"""
SENECIO ORACLE — ACT XXV: TradeJournal (priority 4)
====================================================

Append-only ledger of every trade (entry + exit + audit) the
ExecutionEngine emits. Backed by a local JSONL file with optional
Supabase mirror (best-effort, non-blocking).

Records per ACT-XXV spec:
  - PnL               : realized_pnl_usd + unrealized_pnl_usd (at exit time)
  - fees              : entry_fee_usd + exit_fee_usd
  - spread            : bid-ask spread at entry (bps)
  - MAE               : Maximum Adverse Excursion (worst price against position)
  - MFE               : Maximum Favorable Excursion (best price for position)
  - holding_time      : seconds from entry to exit
  - exit_reason       : STOP / TARGET / TIME_STOP / MANUAL / KILL_SWITCH
  - execution_latency : ack latency in ms (entry + exit)

Schema:
  Each journal entry is a dict with these top-level fields:
    {
      "journal_id": "jr-...",
      "trade_id": "pos-...",                # matches Position.position_id
      "prediction_id": <FK to oracle_predictions.id>,
      "symbol": "ETH/USDT",
      "direction": "LONG" | "SHORT",
      "entry_ts": "ISO 8601",
      "exit_ts": "ISO 8601",
      "holding_time_s": int,
      "entry_price": float,
      "exit_price": float,
      "qty": float,
      "notional_usd": float,
      "realized_pnl_usd": float,
      "realized_pnl_pct": float,            # pnl / risk_usd
      "entry_fee_usd": float,
      "exit_fee_usd": float,
      "total_fees_usd": float,
      "spread_bps_entry": float,
      "slippage_bps_entry": float,
      "slippage_bps_exit": float,
      "mae_price": float,
      "mfe_price": float,
      "mae_bps": float,                     # adverse excursion in bps from entry
      "mfe_bps": float,                     # favorable excursion in bps from entry
      "exit_reason": str,
      "execution_latency_ms_entry": int,
      "execution_latency_ms_exit": int,
      "audit_trail": list[dict],
      "created_at": "ISO 8601",
    }

The journal is the canonical source for PortfolioAnalytics — every
metric (Sharpe, Sortino, PF, etc.) is computed from this ledger.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("senecio.trade_journal")


DEFAULT_JOURNAL_PATH = "data/journal/trades.jsonl"


class TradeJournal:
    """Append-only ledger of closed trades.

    Usage:
        journal = TradeJournal(path="data/journal/trades.jsonl")
        # ExecutionEngine emits audit events; the journal is registered as
        # the audit listener:
        engine.set_audit_listener(journal.on_audit_event)
        # On POSITION_EXIT, the journal writes a full trade record.
        # Query:
        recent = journal.fetch_recent(limit=50)
        all_trades = journal.fetch_all()
    """

    def __init__(
        self,
        path: str = DEFAULT_JOURNAL_PATH,
        supabase_mirror: bool = False,
        supabase_table: str = "oracle_trades",
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.supabase_mirror = supabase_mirror
        self.supabase_table = supabase_table
        # Pending state: position_id → entry audit (so we can join on exit)
        self._pending: dict[str, dict] = {}
        log.info(
            "TradeJournal init: path=%s supabase_mirror=%s",
            self.path, self.supabase_mirror,
        )

    # -------- audit listener interface --------

    def on_audit_event(self, event: dict) -> None:
        """Called by ExecutionEngine on every state transition.

        We care about: POSITION_OPEN (records entry audit) and POSITION_EXIT
        (writes the full trade record).
        """
        try:
            evt = event.get("event", "")
            if evt == "POSITION_OPEN":
                self._handle_open(event)
            elif evt == "POSITION_EXIT":
                self._handle_exit(event)
            # Other events (FILL, ORDER_*) are recorded inside the position's
            # audit_trail, which we pick up at exit time.
        except Exception as e:
            log.exception("journal on_audit_event error: %s", e)

    def _handle_open(self, event: dict) -> None:
        pos = event.get("position") or {}
        pid = pos.get("position_id")
        if not pid:
            return
        self._pending[pid] = {
            "position_id": pid,
            "symbol": pos.get("symbol"),
            "direction": pos.get("direction"),
            "entry_ts": pos.get("entry_ts"),
            "entry_price": pos.get("avg_entry_price"),
            "qty": pos.get("qty"),
            "stop_price": pos.get("stop_price"),
            "target_price": pos.get("target_price"),
            "proposal_id": pos.get("proposal_id"),
            "audit_trail": list(pos.get("audit_trail") or []),
        }

    def _handle_exit(self, event: dict) -> None:
        pos = event.get("position") or {}
        pid = pos.get("position_id")
        if not pid:
            return
        entry = self._pending.pop(pid, {})
        if not entry:
            log.warning("POSITION_EXIT without matching POSITION_OPEN: %s", pid)
            entry = {"position_id": pid}

        record = self._build_record(entry, pos, event)
        self._append(record)

    # -------- record builder --------

    @staticmethod
    def _build_record(entry: dict, exit_pos: dict, exit_event: dict) -> dict:
        entry_ts = entry.get("entry_ts") or exit_pos.get("entry_ts")
        exit_ts = exit_pos.get("exit_ts") or exit_event.get("ts")
        holding_s = 0
        try:
            if entry_ts and exit_ts:
                e_dt = datetime.fromisoformat(str(entry_ts).replace("Z", "+00:00"))
                x_dt = datetime.fromisoformat(str(exit_ts).replace("Z", "+00:00"))
                holding_s = int((x_dt - e_dt).total_seconds())
        except Exception:
            pass

        entry_price = float(entry.get("entry_price") or 0)
        exit_price = float(exit_pos.get("exit_price") or exit_event.get("exit_price") or 0)
        qty = float(entry.get("qty") or 0)
        direction = (entry.get("direction") or exit_pos.get("direction") or "LONG").upper()
        realized_pnl = float(exit_pos.get("realized_pnl") or 0)
        risk_usd = float(exit_pos.get("risk_usd") or 0)

        # MAE/MFE
        mae_price = float(exit_pos.get("mae_price") or entry_price)
        mfe_price = float(exit_pos.get("mfe_price") or entry_price)
        if direction == "LONG":
            mae_bps = ((mae_price - entry_price) / entry_price * 10_000) if entry_price > 0 else 0
            mfe_bps = ((mfe_price - entry_price) / entry_price * 10_000) if entry_price > 0 else 0
        else:
            mae_bps = ((entry_price - mae_price) / entry_price * 10_000) if entry_price > 0 else 0
            mfe_bps = ((entry_price - mfe_price) / entry_price * 10_000) if entry_price > 0 else 0

        # Slippage / latency from audit trail
        audit_trail = list(exit_pos.get("audit_trail") or [])
        entry_slip_bps = 0.0
        exit_slip_bps = float(exit_event.get("exit_slip_bps") or 0)
        entry_latency_ms = 0
        exit_latency_ms = 0
        for evt in audit_trail:
            if evt.get("event") == "FILL":
                entry_slip_bps = max(entry_slip_bps, float(evt.get("slippage_bps") or 0))
                entry_latency_ms = max(entry_latency_ms, int(evt.get("latency_ms") or 0))

        # Spread estimate — if not recorded, derive from slippage
        spread_bps_entry = entry_slip_bps * 2  # round-trip slippage approximates spread

        # Fees — split between entry and exit (taker both sides by default)
        total_fees = float(exit_pos.get("fees_paid") or 0)
        # Approximate split: entry half + exit half
        entry_fee = total_fees / 2 if total_fees > 0 else 0
        exit_fee = total_fees - entry_fee

        notional = qty * entry_price
        realized_pnl_pct = (realized_pnl / risk_usd * 100) if risk_usd > 0 else 0.0

        trade_id = (
            entry.get("position_id")
            or exit_pos.get("position_id")
            or f"pos-{uuid.uuid4().hex[:8]}"
        )

        return {
            "journal_id": f"jr-{uuid.uuid4().hex[:12]}",
            "trade_id": trade_id,
            "prediction_id": entry.get("proposal_id") or exit_pos.get("proposal_id"),
            "symbol": entry.get("symbol") or exit_pos.get("symbol"),
            "direction": direction,
            "entry_ts": entry_ts,
            "exit_ts": exit_ts,
            "holding_time_s": holding_s,
            "entry_price": round(entry_price, 6),
            "exit_price": round(exit_price, 6),
            "qty": round(qty, 8),
            "notional_usd": round(notional, 2),
            "realized_pnl_usd": round(realized_pnl, 2),
            "realized_pnl_pct": round(realized_pnl_pct, 2),
            "risk_usd": round(risk_usd, 2),
            "entry_fee_usd": round(entry_fee, 4),
            "exit_fee_usd": round(exit_fee, 4),
            "total_fees_usd": round(total_fees, 4),
            "spread_bps_entry": round(spread_bps_entry, 2),
            "slippage_bps_entry": round(entry_slip_bps, 2),
            "slippage_bps_exit": round(exit_slip_bps, 2),
            "mae_price": round(mae_price, 6),
            "mfe_price": round(mfe_price, 6),
            "mae_bps": round(mae_bps, 2),
            "mfe_bps": round(mfe_bps, 2),
            "exit_reason": exit_pos.get("exit_reason") or exit_event.get("exit_reason"),
            "execution_latency_ms_entry": entry_latency_ms,
            "execution_latency_ms_exit": exit_latency_ms,
            "audit_trail": audit_trail,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    # -------- persistence --------

    def _append(self, record: dict) -> None:
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            log.info(
                "journal record written: %s %s pnl=$%.2f reason=%s holding=%ds",
                record.get("symbol"), record.get("direction"),
                record.get("realized_pnl_usd"), record.get("exit_reason"),
                record.get("holding_time_s"),
            )
        except Exception as e:
            log.exception("journal append failed: %s", e)
            return

        if self.supabase_mirror:
            try:
                import asyncio
                asyncio.get_event_loop().create_task(self._mirror_to_supabase(record))
            except Exception:
                # Best-effort — never block on Supabase
                pass

    async def _mirror_to_supabase(self, record: dict) -> None:
        """Best-effort mirror to Supabase oracle_trades table (if it exists)."""
        try:
            from .. import supabase_client
            c = supabase_client._get_client()
            r = await c.post(f"/{self.supabase_table}", json=record)
            if r.status_code in (200, 201):
                log.debug("supabase mirror OK trade_id=%s", record.get("trade_id"))
            else:
                log.debug(
                    "supabase mirror non-2xx status=%s body=%s (table may not exist)",
                    r.status_code, r.text[:200],
                )
        except Exception as e:
            log.debug("supabase mirror skipped: %s", e)

    # -------- queries --------

    def fetch_recent(self, limit: int = 50) -> list[dict]:
        """Return last N trades (most recent first)."""
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        rows.reverse()
        return rows[:limit]

    def fetch_all(self) -> list[dict]:
        """Return all trades (chronological)."""
        if not self.path.exists():
            return []
        rows: list[dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows

    def fetch_by_symbol(self, symbol: str, limit: int = 100) -> list[dict]:
        all_rows = self.fetch_all()
        return [r for r in all_rows if r.get("symbol") == symbol][-limit:]

    def fetch_by_direction(self, direction: str, limit: int = 100) -> list[dict]:
        all_rows = self.fetch_all()
        d = direction.upper()
        return [r for r in all_rows if (r.get("direction") or "").upper() == d][-limit:]

    def stats(self) -> dict[str, Any]:
        """High-level journal stats."""
        rows = self.fetch_all()
        if not rows:
            return {"total_trades": 0}
        wins = [r for r in rows if r.get("realized_pnl_usd", 0) > 0]
        losses = [r for r in rows if r.get("realized_pnl_usd", 0) < 0]
        total_pnl = sum(r.get("realized_pnl_usd", 0) for r in rows)
        total_fees = sum(r.get("total_fees_usd", 0) for r in rows)
        return {
            "total_trades": len(rows),
            "total_wins": len(wins),
            "total_losses": len(losses),
            "win_rate_pct": round(len(wins) / len(rows) * 100, 2) if rows else 0.0,
            "total_pnl_usd": round(total_pnl, 2),
            "total_fees_usd": round(total_fees, 2),
            "net_pnl_usd": round(total_pnl - total_fees, 2),
            "avg_holding_time_s": round(
                sum(r.get("holding_time_s", 0) for r in rows) / len(rows), 1
            ) if rows else 0,
            "avg_mae_bps": round(
                sum(r.get("mae_bps", 0) for r in rows) / len(rows), 2
            ) if rows else 0,
            "avg_mfe_bps": round(
                sum(r.get("mfe_bps", 0) for r in rows) / len(rows), 2
            ) if rows else 0,
        }
