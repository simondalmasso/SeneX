from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from senecio_polymarket.backend.gptrader.decisions import DecisionService
from senecio_polymarket.backend.gptrader.mcp_http import build_mcp_app, create_app_from_env
from senecio_polymarket.backend.gptrader.science import sample_gate, summarize_resolved_sample
from senecio_polymarket.backend.gptrader.store import GPTraderStore
from senecio_polymarket.backend.gptrader.verdict import Verdict, evaluate_verdict


class BlindBook:
    def __init__(self, *, cash=10000.0, equity=10000.0, pnl=0.0, open_count=1):
        self.cash = cash
        self.equity = equity
        self.pnl = pnl
        self.open_count = open_count

    def state(self, *, public: bool = False):
        return {
            "paper_only": True,
            "simulation_only": True,
            "live": False,
            "orders_enabled": False,
            "cash": self.cash,
            "equity": self.equity,
            "realized_pnl_usd": self.pnl,
            "normalized_realized_return_pct": self.pnl / 100.0,
            "recent_win_count": 9,
            "recent_loss_count": 2,
            "open_count": self.open_count,
            "risk_state": {"kill_switch_active": False},
            "last_run_id": "run-x",
        }


def test_impossible_sample_geometry_fails_closed() -> None:
    gate = sample_gate(600, 14)
    assert gate.passed is False
    assert gate.verdict == "IMPOSSIBLE_SAMPLE_GEOMETRY"
    result = evaluate_verdict({
        "independent_1h": 600,
        "calendar_days": 14,
        "signal_evidence": True,
        "incremental_established": True,
        "fixed_risk_beats_abstain": True,
        "dependence_aware_ci_excludes_zero": True,
        "cost_stress_2x_sign_stable": True,
    })
    assert result.verdict is Verdict.INDETERMINATE
    assert "IMPOSSIBLE_SAMPLE_GEOMETRY" in result.reasons


@pytest.mark.parametrize(("independent_1h","calendar_days","expected"), [
    (336,14,"INSUFFICIENT_DATA"),
    (599,25,"INSUFFICIENT_DATA"),
    (600,25,"GATE_OPEN"),
])
def test_sample_geometry_boundaries(independent_1h, calendar_days, expected) -> None:
    assert sample_gate(independent_1h, calendar_days).verdict == expected


def test_summarized_sample_geometry_is_always_physically_valid() -> None:
    rows=[]
    for day in range(1,15):
        for hour in range(24):
            ts=f"2026-09-{day:02d}T{hour:02d}:00:00Z"
            rows.append({"timestamp":ts,"symbol":"BTCUSDT","resolved":True})
            rows.append({"timestamp":ts,"symbol":"ETHUSDT","resolved":True})
    summary=summarize_resolved_sample(rows)
    assert summary.independent_1h <= 24 * summary.calendar_days
    assert summary.independent_1h == 336
    assert summary.calendar_days == 14


def test_decision_state_is_blind_to_performance_side_channels(tmp_path) -> None:
    service=DecisionService(GPTraderStore(tmp_path), paper_book=BlindBook(cash=123.0,equity=456.0,pnl=99.0))
    before=service.get_gptrader_state()
    service.paper_book=BlindBook(cash=999999.0,equity=1.0,pnl=-999.0)
    after=service.get_gptrader_state()
    assert before == after
    encoded=json.dumps(before,sort_keys=True).lower()
    for forbidden in ("cash","equity","pnl","return","win","loss","outcome"):
        assert forbidden not in encoded
    assert before["open_count"] == 1
    assert before["live"] is False


def test_auth_token_boundary_and_hidden_docs(tmp_path) -> None:
    service=DecisionService(GPTraderStore(tmp_path), paper_book=BlindBook())
    with pytest.raises(ValueError):
        build_mcp_app(service, token="x"*31)
    app=build_mcp_app(service, token="x"*32)
    client=TestClient(app)
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    payload={"jsonrpc":"2.0","id":1,"method":"tools/list"}
    assert client.post("/mcp",json=payload).status_code == 401
    assert client.post("/mcp",headers={"Authorization":"Bearer bad"},json=payload).status_code == 401
    assert client.post("/mcp",headers={"Authorization":"Bearer "+("x"*32)},json=payload).status_code == 200


def test_missing_env_fails_startup(monkeypatch) -> None:
    monkeypatch.delenv("SENEX_GPTRADER_MCP_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="SENEX_GPTRADER_MCP_TOKEN"):
        create_app_from_env()


def test_short_env_fails_startup(monkeypatch) -> None:
    monkeypatch.setenv("SENEX_GPTRADER_MCP_TOKEN", "x"*31)
    with pytest.raises(ValueError, match="32"):
        create_app_from_env()


def test_decision_mcp_schema_has_no_size_scale(tmp_path) -> None:
    service=DecisionService(GPTraderStore(tmp_path), paper_book=BlindBook())
    app=build_mcp_app(service, token="x"*32)
    client=TestClient(app)
    response=client.post("/mcp",headers={"Authorization":"Bearer "+("x"*32)},json={"jsonrpc":"2.0","id":1,"method":"tools/list"})
    tools=response.json()["result"]["tools"]
    submit=next(tool for tool in tools if tool["name"]=="submit_paper_decisions")
    properties=submit["inputSchema"]["properties"]["decisions"]["items"]["properties"]
    assert "size_scale" not in properties
