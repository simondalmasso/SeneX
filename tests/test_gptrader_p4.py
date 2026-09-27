from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from senecio_polymarket.backend.gptrader.decisions import (
    ConflictingDecisionError,
    CursorMismatchError,
    DecisionService,
    DecisionValidationError,
)
from senecio_polymarket.backend.gptrader.mcp_http import build_mcp_app
from senecio_polymarket.backend.gptrader.sealer import PacketSealer
from senecio_polymarket.backend.gptrader.store import GPTraderStore


def row(ts: str, symbol: str = "BTCUSDT") -> dict:
    return {
        "timestamp": ts,
        "symbol": symbol,
        "prediction": "LONG",
        "confidence": 0.61,
        "ev": 0.01,
        "price_now": 100.0,
        "_audit": {
            "confidence_semantics_v1": {"semantics": "RAW_CONVICTION"},
            "outcomes_dual": {},
        },
        "outcome": None,
        "price_1h_later": None,
    }


class FakeBook:
    def __init__(self):
        self.calls: list[str] = []

    async def apply_decision(self, packet: dict, decision: dict, *, run_id: str, persist_decision: bool = False):
        self.calls.append(packet["packet_id"])
        return {
            "classification": "TAKE_ACCEPTED" if decision["action"] == "TAKE" else "ABSTAIN",
            "packet_id": packet["packet_id"],
        }

    def state(self, *, public: bool = False) -> dict:
        return {
            "paper_only": True,
            "simulation_only": True,
            "live": False,
            "orders_enabled": False,
            "cash": 9999.0,
            "equity": 10001.0,
            "open_count": 1,
            "risk_state": {"kill_switch_active": False},
            "last_run_id": "run-x",
        }


def decision(packet_id: str, action: str = "TAKE", **extra) -> dict:
    return {
        "packet_id": packet_id,
        "action": action,
        "reason_codes": ["TEST"],
        "idempotency_key": f"idem-{packet_id}",
        **extra,
    }


def setup_service(tmp_path: Path, count: int = 2):
    store = GPTraderStore(tmp_path)
    sealer = PacketSealer(root=tmp_path)
    packets = []
    for idx in range(count):
        packets.append(sealer.seal(row(f"2026-09-26T2{idx}:00:00+00:00")))
    book = FakeBook()
    return store, packets, book, DecisionService(store, paper_book=book)


def test_health_and_batch_are_decision_safe_and_bounded(tmp_path: Path) -> None:
    store, packets, _, svc = setup_service(tmp_path)
    health = svc.get_gptrader_health()
    assert health["ready"] is True
    assert health["paper_only"] is True
    assert health["live"] is False
    encoded = repr(health).lower()
    assert "outcome" not in encoded
    assert "current_price" not in encoded

    batch = svc.get_prediction_batch(cursor=None, limit=2)
    assert [p["packet_id"] for p in batch["packets"]] == [p["packet_id"] for p in packets]
    assert batch["cursor_in"] == svc.cursor_for_seq(0)
    assert batch["next_cursor"] == svc.cursor_for_seq(2)
    assert batch["has_more"] is False
    assert 0 < len(batch["batch_id"]) <= 64
    assert "outcome" not in repr(batch).lower()
    assert "price_1h_later" not in repr(batch)


def test_decision_safe_state_has_no_recent_results(tmp_path: Path) -> None:
    _, _, _, svc = setup_service(tmp_path, count=1)
    state = svc.get_gptrader_state()
    assert state["paper_only"] is True
    assert state["live"] is False
    assert state["open_count"] == 1
    encoded = repr(state).lower()
    for forbidden in (
        "cash",
        "equity",
        "outcome",
        "realized_pnl",
        "return",
        "win",
        "loss",
        "price_1h_later",
        "trades",
    ):
        assert forbidden not in encoded


def test_duplicate_retry_is_one_durable_decision_and_one_fill(tmp_path: Path) -> None:
    store, packets, book, svc = setup_service(tmp_path, count=1)
    cursor = svc.cursor_for_seq(0)
    first = asyncio.run(svc.submit_paper_decisions("run-1", cursor, [decision(packets[0]["packet_id"])]))
    retry = asyncio.run(svc.submit_paper_decisions("run-1-retry", cursor, [decision(packets[0]["packet_id"])]))
    assert first["applied"] == 1
    assert retry["duplicate"] is True
    assert len(store.read_decisions()) == 1
    assert book.calls == [packets[0]["packet_id"]]
    assert store.cursor_seq() == 1


def test_conflicting_retry_fails_closed(tmp_path: Path) -> None:
    store, packets, _, svc = setup_service(tmp_path, count=1)
    cursor = svc.cursor_for_seq(0)
    asyncio.run(svc.submit_paper_decisions("run-1", cursor, [decision(packets[0]["packet_id"], "ABSTAIN")]))
    with pytest.raises(ConflictingDecisionError):
        asyncio.run(svc.submit_paper_decisions("run-2", cursor, [decision(packets[0]["packet_id"], "TAKE")]))
    assert len(store.read_decisions()) == 1


def test_cursor_mismatch_and_nonprefix_batch_apply_zero(tmp_path: Path) -> None:
    store, packets, book, svc = setup_service(tmp_path, count=2)
    with pytest.raises(CursorMismatchError):
        asyncio.run(svc.submit_paper_decisions("run-bad", svc.cursor_for_seq(99), [decision(packets[0]["packet_id"])]))
    with pytest.raises(CursorMismatchError):
        asyncio.run(svc.submit_paper_decisions("run-skip", svc.cursor_for_seq(0), [decision(packets[1]["packet_id"])]))
    assert store.read_decisions() == []
    assert store.cursor_seq() == 0
    assert book.calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"action": "FLIP"},
        {"action": "LIVE"},
        {"direction": "SHORT"},
        {"direction_override": "SHORT"},
        {"wallet": "x"},
        {"signer": "x"},
        {"broker": "x"},
        {"notional": 1000},
        {"model_weights": {"x": 1}},
        {"d1": "query"},
        {"url": "https://example.com"},
        {"shell": "echo nope"},
        {"size_scale": 2.0},
    ],
)
def test_forbidden_decision_fields_fail_closed(tmp_path: Path, extra: dict) -> None:
    _, packets, _, svc = setup_service(tmp_path, count=1)
    payload = decision(packets[0]["packet_id"])
    payload.update(extra)
    with pytest.raises(DecisionValidationError):
        svc.validate_decision(payload)


def test_simultaneous_duplicate_submit_fills_once(tmp_path: Path) -> None:
    store, packets, book, svc = setup_service(tmp_path, count=1)
    cursor = svc.cursor_for_seq(0)
    payload = [decision(packets[0]["packet_id"])]

    async def run_both():
        return await asyncio.gather(
            svc.submit_paper_decisions("run-a", cursor, payload),
            svc.submit_paper_decisions("run-b", cursor, payload),
        )

    results = asyncio.run(run_both())
    assert sorted([bool(r.get("duplicate")) for r in results]) == [False, True]
    assert len(store.read_decisions()) == 1
    assert len(book.calls) == 1


def test_crash_after_decision_commit_recovers_without_duplicate_fill(tmp_path: Path) -> None:
    store, packets, _, _ = setup_service(tmp_path, count=1)
    fired = {"value": False}

    def crash_once(event: str):
        if event == "after_decision_commit" and not fired["value"]:
            fired["value"] = True
            raise RuntimeError("synthetic crash")

    first_book = FakeBook()
    crashing = DecisionService(store, paper_book=first_book, fault_hook=crash_once)
    cursor = crashing.cursor_for_seq(0)
    with pytest.raises(RuntimeError, match="synthetic crash"):
        asyncio.run(crashing.submit_paper_decisions("run-crash", cursor, [decision(packets[0]["packet_id"])]))
    assert len(store.read_decisions()) == 1
    assert store.cursor_seq() == 0
    assert first_book.calls == []


    restarted_book = FakeBook()
    restarted = DecisionService(store, paper_book=restarted_book)
    result = asyncio.run(restarted.submit_paper_decisions("run-recover", cursor, [decision(packets[0]["packet_id"])]))
    assert result["recovered"] is True
    assert restarted_book.calls == [packets[0]["packet_id"]]
    assert store.cursor_seq() == 1


def test_crash_before_decision_commit_leaves_zero_mutation(tmp_path: Path) -> None:
    store, packets, book, _ = setup_service(tmp_path, count=1)

    def crash(event: str):
        if event == "before_decision_commit":
            raise RuntimeError("precommit crash")

    svc = DecisionService(store, paper_book=book, fault_hook=crash)
    with pytest.raises(RuntimeError, match="precommit crash"):
        asyncio.run(svc.submit_paper_decisions("run-crash", svc.cursor_for_seq(0), [decision(packets[0]["packet_id"])]))
    assert store.read_decisions() == []
    assert store.cursor_seq() == 0
    assert book.calls == []


def test_settlement_read_requires_durable_decision(tmp_path: Path) -> None:
    _, packets, _, svc = setup_service(tmp_path, count=1)
    seen: list[str] = []

    def reader(packet: dict):
        seen.append(packet["packet_id"])
        return {"outcome": "CORRECT"}

    with pytest.raises(RuntimeError, match="DECISION_NOT_COMMITTED"):
        svc.read_settlement_after_decision(packets[0]["packet_id"], reader)
    assert seen == []
    cursor = svc.cursor_for_seq(0)
    asyncio.run(svc.submit_paper_decisions("run-1", cursor, [decision(packets[0]["packet_id"], "ABSTAIN")]))
    result = svc.read_settlement_after_decision(packets[0]["packet_id"], reader)
    assert result["outcome"] == "CORRECT"
    assert seen == [packets[0]["packet_id"]]


def test_mcp_http_requires_auth_and_exposes_only_four_tools(tmp_path: Path) -> None:
    _, _, _, svc = setup_service(tmp_path, count=1)
    token = "x" * 32
    app = build_mcp_app(svc, token=token)
    client = TestClient(app)
    assert client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401
    response = client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert names == {
        "get_gptrader_health",
        "get_prediction_batch",
        "get_gptrader_state",
        "submit_paper_decisions",
    }


def test_gptrader_p4_has_no_d1_broker_wallet_or_shell_dispatch(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1] / "senecio_polymarket" / "backend" / "gptrader"
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py")).lower()
    for forbidden in (
        "supabase_client",
        "create_market_order",
        "fetch_balance",
        "withdraw(",
        "subprocess.",
        "os.system",
        "eval(",
        "exec(",
    ):
        assert forbidden not in text


def test_mcp_healthz_is_minimal_and_paper_only(tmp_path: Path) -> None:
    _, _, _, svc = setup_service(tmp_path, count=1)
    app = build_mcp_app(svc, token="x" * 32)
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "service": "senex-gptrader-decision",
        "paper_only": True,
        "simulation_only": True,
        "live": False,
    }
