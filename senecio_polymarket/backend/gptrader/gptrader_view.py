from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .paths import GPTraderPaths
from .store import GPTraderStore

_PUBLIC_FUTURE_KEYS = frozenset(
    {
        "outcome",
        "outcome_15m",
        "outcome_1h",
        "outcomes_dual",
        "price_15m_later",
        "price_1h_later",
        "future_price",
        "future_prices",
        "post_t0_price",
        "post_prediction_price",
        "settlement",
        "settlement_proof",
        "settlement_cas",
        "resolution_price",
        "resolved_at",
        "review_payload",
    }
)


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(path: Path) -> list[dict[str, Any]] | None:
    if not path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return None
    return rows


def _scrub_future(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _scrub_future(child)
            for key, child in value.items()
            if str(key).lower() not in _PUBLIC_FUTURE_KEYS
        }
    if isinstance(value, list):
        return [_scrub_future(child) for child in value]
    return value


def _public_verdict(paths: GPTraderPaths) -> dict[str, Any]:
    raw = _read_json(paths.verdict)
    if raw is None:
        return {
            "verdict": "INSUFFICIENT_DATA",
            "edge": "UNPROVEN",
            "sample_count": None,
            "calendar_days": None,
        }
    verdict = str(raw.get("verdict") or "INSUFFICIENT_DATA")
    return {
        "verdict": verdict,
        "edge": "UNPROVEN",
        "sample_count": raw.get("independent_1h", raw.get("sample_count")),
        "calendar_days": raw.get("calendar_days"),
    }


def gptrader_public_verdict(root: str | Path | None = None) -> dict[str, Any]:
    return _public_verdict(GPTraderPaths.from_root(root))


def gptrader_public_trades(
    root: str | Path | None = None,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("limit must be between 1 and 50")
    paths = GPTraderPaths.from_root(root)
    rows = _read_jsonl(paths.trades)
    selected = [] if rows is None else rows[-limit:]
    return {
        "label": "GPTrader PAPER/HYPOTHETICAL — TREATMENT",
        "paper_only": True,
        "simulation_only": True,
        "live": False,
        "count": None if rows is None else len(selected),
        "trades": _scrub_future(selected),
    }


def gptrader_public_state(root: str | Path | None = None) -> dict[str, Any]:
    paths = GPTraderPaths.from_root(root)
    paper = _read_json(paths.root / "paper_state.json")
    try:
        decisions = GPTraderStore(paths.root).read_decisions()
        decision_log_status = "OK"
    except Exception:
        decisions = None
        decision_log_status = "QUARANTINED"
    packets = _read_jsonl(paths.sealed_packets)
    trades = _read_jsonl(paths.trades)
    verdict = _public_verdict(paths)

    any_state = any(value is not None for value in (paper, decisions, packets, trades))
    takes = None if decisions is None else sum(
        str(row.get("action") or "").upper() == "TAKE" for row in decisions
    )
    abstains = None if decisions is None else sum(
        str(row.get("action") or "").upper() == "ABSTAIN" for row in decisions
    )
    rejects = None if decisions is None else sum(
        row.get("classification") == "TAKE_REJECTED_BY_KERNEL"
        for row in decisions
    )

    open_count = None
    closed_count = None
    scale_label = "PRIMARY_NORMALIZED"
    last_run_id = None
    max_drawdown = None
    normalized_return = None
    if paper is not None:
        scale_label = str(paper.get("scale_label") or "PRIMARY_NORMALIZED")
        positions = paper.get("positions")
        closed = paper.get("closed_positions")
        open_count = len(positions) if isinstance(positions, dict) else None
        closed_count = len(closed) if isinstance(closed, list) else None
        last_run_id = paper.get("last_run_id")
        risk_state = paper.get("risk_state")
        if isinstance(risk_state, dict):
            max_drawdown = risk_state.get("drawdown_pct")

        starting = paper.get("starting_cash")
        if trades is not None and isinstance(starting, (int, float)) and float(starting) > 0:
            realized = 0.0
            for row in trades:
                value = row.get("realized_pnl_usd")
                if isinstance(value, (int, float)):
                    realized += float(value)
            normalized_return = round(realized / float(starting) * 100.0, 6)

    return {
        "status": "OK" if any_state else "UNKNOWN",
        "label": "GPTrader PAPER/HYPOTHETICAL — TREATMENT",
        "control_label": "SENEX native PAPER — CONTROL",
        "version": "gptrader.observability.v1",
        "paper_only": True,
        "simulation_only": True,
        "live": False,
        "orders_enabled": False,
        "edge": "UNPROVEN",
        "scale_label": scale_label,
        "last_run_id": last_run_id,
        "packet_count": None if packets is None else len(packets),
        "take_count": takes,
        "abstain_count": abstains,
        "kernel_reject_count": rejects,
        "decision_log_status": decision_log_status,
        "open_hypothetical_positions": open_count,
        "closed_hypothetical_positions": closed_count,
        "normalized_realized_return_pct": normalized_return,
        "max_drawdown_pct": max_drawdown,
        "sample_count": verdict["sample_count"],
        "calendar_days": verdict["calendar_days"],
        "verdict": verdict["verdict"],
        "safety": {
            "paper_only": True,
            "simulation_only": True,
            "live": False,
            "orders_enabled": False,
        },
    }
