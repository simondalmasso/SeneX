from __future__ import annotations

import json
from pathlib import Path

from senecio_polymarket.backend.gptrader.gptrader_view import (
    gptrader_public_state,
    gptrader_public_trades,
    gptrader_public_verdict,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_missing_files_preserve_unknown_not_zero(tmp_path: Path) -> None:
    state = gptrader_public_state(tmp_path)
    assert state["status"] == "UNKNOWN"
    assert state["packet_count"] is None
    assert state["take_count"] is None
    assert state["abstain_count"] is None
    assert state["open_hypothetical_positions"] is None
    assert state["normalized_realized_return_pct"] is None
    assert state["verdict"] == "INSUFFICIENT_DATA"


def test_observational_state_counts_treatment_without_private_owner_cash(
    tmp_path: Path,
) -> None:
    _write_json(
        tmp_path / "paper_state.json",
        {
            "scale_label": "OWNER_SCALE_PRIVATE",
            "starting_cash": 123.45,
            "cash": 120.00,
            "positions": {"BTCUSDT": {"status": "OPEN"}},
            "closed_positions": [{"status": "CLOSED"}],
            "risk_state": {"drawdown_pct": 1.25},
            "last_run_id": "run-1",
        },
    )
    _write_jsonl(
        tmp_path / "decisions.jsonl",
        [
            {"action": "TAKE", "classification": "TAKE_ACCEPTED"},
            {"action": "TAKE", "classification": "TAKE_REJECTED_BY_KERNEL"},
            {"action": "ABSTAIN", "classification": "ABSTAIN"},
        ],
    )
    _write_jsonl(tmp_path / "sealed_packets.jsonl", [{"packet_id": "a"}, {"packet_id": "b"}])
    _write_jsonl(
        tmp_path / "trades.jsonl",
        [
            {"realized_pnl_usd": 5.0, "total_fees_usd": 1.0},
            {"realized_pnl_usd": -2.0, "total_fees_usd": 1.0},
        ],
    )

    state = gptrader_public_state(tmp_path)
    encoded = json.dumps(state, sort_keys=True)
    assert state["label"] == "GPTrader PAPER/HYPOTHETICAL — TREATMENT"
    assert state["packet_count"] == 2
    assert state["take_count"] == 2
    assert state["abstain_count"] == 1
    assert state["kernel_reject_count"] == 1
    assert state["open_hypothetical_positions"] == 1
    assert state["closed_hypothetical_positions"] == 1
    assert state["scale_label"] == "OWNER_SCALE_PRIVATE"
    assert state["normalized_realized_return_pct"] is not None
    assert "123.45" not in encoded
    assert "starting_cash" not in encoded
    assert '"cash"' not in encoded


def test_public_trades_strip_future_or_outcome_fields_recursively(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "trades.jsonl",
        [
            {
                "trade_id": "t1",
                "symbol": "BTCUSDT",
                "realized_pnl_usd": 1.0,
                "outcome": "CORRECT",
                "nested": {"price_1h_later": 101.0, "safe": "ok"},
            }
        ],
    )
    payload = gptrader_public_trades(tmp_path, limit=10)
    encoded = json.dumps(payload, sort_keys=True)
    assert "outcome" not in encoded
    assert "price_1h_later" not in encoded
    assert payload["trades"][0]["nested"]["safe"] == "ok"


def test_verdict_missing_is_insufficient_data_not_edge(tmp_path: Path) -> None:
    verdict = gptrader_public_verdict(tmp_path)
    assert verdict["verdict"] == "INSUFFICIENT_DATA"
    assert verdict["edge"] == "UNPROVEN"
    assert verdict["sample_count"] is None
    assert verdict["calendar_days"] is None


def test_public_fastapi_surface_is_get_only() -> None:
    import ast

    main_real = (
        Path(__file__).resolve().parents[1]
        / "senecio_polymarket"
        / "backend"
        / "main_real.py"
    )
    tree = ast.parse(main_real.read_text(encoding="utf-8"))
    methods: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != "app":
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            route = decorator.args[0].value
            if isinstance(route, str) and route.startswith("/api/gptrader/"):
                methods.setdefault(route, set()).add(decorator.func.attr.lower())
    assert methods == {
        "/api/gptrader/state": {"get"},
        "/api/gptrader/trades": {"get"},
        "/api/gptrader/verdict": {"get"},
    }


def test_dashboard_has_control_treatment_separation() -> None:
    root = Path(__file__).resolve().parents[1] / "senecio_polymarket" / "frontend"
    html = (root / "index.html").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    assert "SENEX native PAPER — CONTROL" in html
    assert "GPTrader PAPER/HYPOTHETICAL — TREATMENT" in html
    assert "/api/gptrader/state" in js
    assert "gptrader-verdict" in html


def test_public_counts_dedupe_identical_legacy_decisions_first_occurrence(tmp_path: Path) -> None:
    first = {
        "ts": "2026-09-27T11:00:00+00:00",
        "run_id": "run-first",
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-public-dedupe",
        "action": "TAKE",
        "idempotency_key": "idem-public-dedupe",
        "decision_hash": "a" * 64,
        "classification": "TAKE_ACCEPTED",
    }
    retry = {**first, "ts": "2026-09-27T11:05:00+00:00", "run_id": "run-retry"}
    _write_jsonl(tmp_path / "decisions.jsonl", [first, retry])

    state = gptrader_public_state(tmp_path)
    assert state["take_count"] == 1
    assert state["abstain_count"] == 0
    assert state["kernel_reject_count"] == 0


def test_view_root_env_points_dashboard_at_consumer_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    _write_jsonl(producer / "sealed_packets.jsonl", [{"packet_id": "producer-only"}])
    _write_jsonl(
        consumer / "sealed_packets.jsonl",
        [{"packet_id": "consumer-1"}, {"packet_id": "consumer-2"}],
    )
    monkeypatch.setenv("SENEX_GPTRADER_VIEW_ROOT", str(consumer))

    state = gptrader_public_state()

    assert state["packet_count"] == 2


def test_mcp_root_env_is_separate_from_producer_results_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from senecio_polymarket.backend.gptrader.mcp_http import create_app_from_env

    producer_base = tmp_path / "producer-base"
    consumer = tmp_path / "consumer"
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(producer_base))
    monkeypatch.setenv("SENEX_GPTRADER_MCP_ROOT", str(consumer))
    monkeypatch.setenv("SENEX_GPTRADER_MCP_TOKEN", "m" * 32)
    monkeypatch.setenv("SENEX_GPTRADER_INGEST_TOKEN", "i" * 32)

    app = create_app_from_env()
    try:
        lease_path = app.state.gptrader_root_lease.path
        assert lease_path.parent == consumer
        assert not (producer_base / "gptrader" / ".gptrader.owner.lock").exists()
    finally:
        app.state.gptrader_root_lease.close()


def test_launcher_supports_isolated_single_worker_mcp_runtime() -> None:
    launcher = (
        Path(__file__).resolve().parents[1]
        / "senecio_polymarket"
        / "start_single_authority.sh"
    ).read_text(encoding="utf-8")

    assert "SENEX_GPTRADER_MCP_PORT" in launcher
    assert "SENEX_GPTRADER_MCP_ROOT" in launcher
    assert "SENEX_GPTRADER_VIEW_ROOT" in launcher
    assert "backend.gptrader.mcp_http:create_app_from_env" in launcher
    assert "--workers 1" in launcher
    assert "partial GPTrader MCP auth configuration" in launcher
