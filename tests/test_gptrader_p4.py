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
from senecio_polymarket.backend.gptrader.store import (
    DecisionLogCorruptionError,
    GPTraderStore,
)


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


def test_decision_log_repairs_missing_corrupt_or_stale_index_after_crash(tmp_path: Path, monkeypatch) -> None:
    store = GPTraderStore(tmp_path)
    row = {
        "ts": "2026-09-27T21:30:00+00:00",
        "run_id": "run-index-crash",
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-index-crash",
        "action": "ABSTAIN",
        "reason_codes": ["TEST"],
        "idempotency_key": "idem-index-crash",
        "decision_hash": "d" * 64,
    }
    original_atomic = store._atomic_json

    def crash_before_index_commit(path: Path, value) -> None:
        if path == store.paths.decisions_index:
            raise RuntimeError("synthetic index crash")
        original_atomic(path, value)

    monkeypatch.setattr(store, "_atomic_json", crash_before_index_commit)
    with pytest.raises(RuntimeError, match="synthetic index crash"):
        store.append_decision(row)
    assert len(store.read_decisions()) == 1

    for index_payload in (None, "{broken", "{}"):
        if index_payload is None:
            store.paths.decisions_index.unlink(missing_ok=True)
        else:
            store.paths.decisions_index.write_text(index_payload, encoding="utf-8")
        restarted = GPTraderStore(tmp_path)
        assert restarted.find_decision("GPTRADER_CHAT_V1", "packet-index-crash") == row
        with pytest.raises(ValueError, match="decision already exists"):
            restarted.append_decision(row)


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


def test_crash_after_paper_apply_before_cursor_does_not_reapply_execution(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    packet = PacketSealer(root=tmp_path).seal(row("2026-09-27T22:00:00+00:00"))
    fired = {"value": False}

    def crash_once(event: str) -> None:
        if event == "after_paper_apply_before_cursor" and not fired["value"]:
            fired["value"] = True
            raise RuntimeError("synthetic post-paper crash")

    crashing = DecisionService(store, fault_hook=crash_once)
    cursor = crashing.cursor_for_seq(0)
    payload = [decision(packet["packet_id"], "TAKE")]
    with pytest.raises(RuntimeError, match="synthetic post-paper crash"):
        asyncio.run(crashing.submit_paper_decisions("run-crash", cursor, payload))

    assert store.cursor_seq() == 0
    state_after_crash = store.load_paper_state()
    assert state_after_crash is not None
    assert state_after_crash["positions"]
    assert state_after_crash["risk_state"]["proposals_evaluated"] == 1

    restarted_store = GPTraderStore(tmp_path)
    restarted = DecisionService(restarted_store)
    result = asyncio.run(
        restarted.submit_paper_decisions("run-recover", cursor, payload)
    )
    state_after_retry = restarted_store.load_paper_state()

    assert result["recovered"] is True
    assert restarted_store.cursor_seq() == 1
    assert state_after_retry is not None
    assert state_after_retry["cash"] == state_after_crash["cash"]
    assert state_after_retry["positions"] == state_after_crash["positions"]
    assert state_after_retry["risk_state"] == state_after_crash["risk_state"]


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


def test_decision_log_recovers_torn_final_line(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    first = {
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-torn-1",
        "action": "ABSTAIN",
        "idempotency_key": "idem-torn-1",
        "decision_hash": "a" * 64,
    }
    store.append_decision(first)
    with open(store.paths.decisions, "ab") as handle:
        handle.write(b'{"policy_id":"GPTRADER_CHAT_V1"')

    restarted = GPTraderStore(tmp_path)
    assert restarted.find_decision("GPTRADER_CHAT_V1", "packet-torn-1") == first

    second = {
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-torn-2",
        "action": "ABSTAIN",
        "idempotency_key": "idem-torn-2",
        "decision_hash": "b" * 64,
    }
    restarted.append_decision(second)
    assert [row["packet_id"] for row in restarted.read_decisions()] == [
        "packet-torn-1",
        "packet-torn-2",
    ]


def test_legacy_identical_duplicate_is_safe_but_conflict_fails_closed(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    row = {
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-legacy",
        "action": "ABSTAIN",
        "idempotency_key": "idem-legacy",
        "decision_hash": "c" * 64,
    }
    store._append_jsonl(store.paths.decisions, row)
    store._append_jsonl(store.paths.decisions, dict(row))

    restarted = GPTraderStore(tmp_path)
    assert restarted.find_decision("GPTRADER_CHAT_V1", "packet-legacy") == row

    conflict = dict(row)
    conflict["decision_hash"] = "d" * 64
    store._append_jsonl(store.paths.decisions, conflict)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        GPTraderStore(tmp_path).find_decision("GPTRADER_CHAT_V1", "packet-legacy")


def test_mcp_raw_body_is_bounded_and_malformed_fails_closed(tmp_path: Path) -> None:
    _, _, _, svc = setup_service(tmp_path, count=1)
    app = build_mcp_app(svc, token="x" * 32)
    client = TestClient(app)
    headers = {"Authorization": "Bearer " + "x" * 32}

    oversized = (
        b'{"jsonrpc":"2.0","id":1,"method":"initialize","x":"'
        + b"x" * (70 * 1024)
        + b'"}'
    )
    assert client.post("/mcp", headers=headers, content=oversized).status_code == 413
    assert client.post("/mcp", headers=headers, content=b'{"jsonrpc":').status_code == 400


def test_newline_terminated_corrupt_final_decision_fails_closed_without_truncate(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    store.paths.decisions.write_bytes(
        b'{"policy_id":"GPTRADER_CHAT_V1","packet_id":"committed-but-corrupt"\n'
    )
    before = store.paths.decisions.read_bytes()

    with pytest.raises(Exception):
        store.read_decisions()

    assert store.paths.decisions.read_bytes() == before

    replacement = {
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "committed-but-corrupt",
        "action": "ABSTAIN",
        "idempotency_key": "idem-replacement",
        "decision_hash": "e" * 64,
    }
    with pytest.raises(Exception):
        store.append_decision(replacement)
    assert store.paths.decisions.read_bytes() == before


def test_first_durable_duplicate_commit_is_authoritative(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    first = {
        "ts": "2026-09-27T10:00:00+00:00",
        "run_id": "run-first",
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-first-wins",
        "action": "ABSTAIN",
        "idempotency_key": "idem-first-wins",
        "decision_hash": "f" * 64,
    }
    retry = {
        **first,
        "ts": "2026-09-27T10:05:00+00:00",
        "run_id": "run-retry",
    }
    store._append_jsonl(store.paths.decisions, first)
    store._append_jsonl(store.paths.decisions, retry)

    found = GPTraderStore(tmp_path).find_decision(
        "GPTRADER_CHAT_V1",
        "packet-first-wins",
    )
    assert found is not None
    assert found["run_id"] == "run-first"
    assert found["ts"] == "2026-09-27T10:00:00+00:00"


def test_missing_paper_state_with_durable_decision_fails_closed(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    packet = PacketSealer(root=tmp_path).seal(row("2026-09-27T23:00:00+00:00"))
    service = DecisionService(store)
    asyncio.run(
        service.submit_paper_decisions(
            "run-before-state-loss",
            service.cursor_for_seq(0),
            [decision(packet["packet_id"], "TAKE")],
        )
    )
    assert store.load_paper_state() is not None
    store.paper_state_path.unlink()

    with pytest.raises(RuntimeError, match="PAPER_STATE"):
        DecisionService(GPTraderStore(tmp_path))


def test_conflicting_legacy_decision_is_visible_in_health_quarantine(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    first = {
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-health-conflict",
        "action": "ABSTAIN",
        "idempotency_key": "idem-health-conflict",
        "decision_hash": "1" * 64,
    }
    conflict = {**first, "decision_hash": "2" * 64}
    store._append_jsonl(store.paths.decisions, first)
    store._append_jsonl(store.paths.decisions, conflict)
    service = DecisionService(store, paper_book=FakeBook())

    health = service.get_gptrader_health()
    assert health["ready"] is False
    assert health["decision_log_health"]["status"] == "QUARANTINED"
    assert health["decision_log_health"]["reason"] == "CONFLICTING_DUPLICATE_DECISION"
    assert health["decision_log_health"]["key"] == "GPTRADER_CHAT_V1|packet-health-conflict"
    assert store.decision_quarantine_path.exists()


def test_corrupt_cursor_is_distinct_from_missing_and_visible_in_health(tmp_path: Path) -> None:
    store = GPTraderStore(tmp_path)
    service = DecisionService(store, paper_book=FakeBook())
    missing = service.get_gptrader_health()
    assert missing["cursor_ready"] is True
    assert missing["cursor_status"] == "MISSING"

    store.paths.cursor.write_text("{broken", encoding="utf-8")
    corrupt = service.get_gptrader_health()
    assert corrupt["ready"] is False
    assert corrupt["cursor_ready"] is False
    assert corrupt["cursor_status"] == "CORRUPT"
    assert corrupt["cursor"] is None



@pytest.mark.parametrize("raw_record", [b"42\n", b"[]\n", b"null\n"])
def test_valid_json_non_object_decision_row_quarantines_and_refuses_append(
    tmp_path: Path,
    raw_record: bytes,
) -> None:
    store = GPTraderStore(tmp_path)
    store.paths.decisions.write_bytes(raw_record)
    before = store.paths.decisions.read_bytes()

    with pytest.raises(DecisionLogCorruptionError):
        store.read_decisions()

    health = store.decision_log_health()
    assert health["ok"] is False
    assert health["status"] == "QUARANTINED"
    assert health["reason"] == "CORRUPT_DECISION_LOG"

    replacement = {
        "policy_id": "GPTRADER_CHAT_V1",
        "packet_id": "packet-non-object",
        "action": "ABSTAIN",
        "idempotency_key": "idem-non-object",
        "decision_hash": "9" * 64,
    }
    with pytest.raises(DecisionLogCorruptionError):
        store.append_decision(replacement)

    assert store.paths.decisions.read_bytes() == before


def test_direct_http_prediction_and_submit_contract_is_auth_bound_and_paper_only(tmp_path: Path) -> None:
    store, packets, book, svc = setup_service(tmp_path, count=1)
    token = "x" * 32
    app = build_mcp_app(svc, token=token)
    client = TestClient(app)

    assert client.get("/v1/predictions/next").status_code == 401

    headers = {"Authorization": f"Bearer {token}"}
    batch_response = client.get(
        "/v1/predictions/next",
        headers=headers,
        params={"limit": 1},
    )
    assert batch_response.status_code == 200
    batch = batch_response.json()
    assert len(batch["packets"]) == 1
    assert batch["packets"][0]["packet_id"] == packets[0]["packet_id"]
    encoded = repr(batch).lower()
    assert "outcome" not in encoded
    assert "price_1h_later" not in encoded
    assert "realized_pnl" not in encoded

    payload = {
        "run_id": "direct-http-run-1",
        "cursor": batch["cursor_in"],
        "decisions": [decision(packets[0]["packet_id"], "ABSTAIN")],
    }
    submit = client.post("/v1/decisions", headers=headers, json=payload)
    assert submit.status_code == 200
    result = submit.json()
    assert result["applied"] == 1
    assert result["duplicate"] is False
    assert store.cursor_seq() == 1
    assert len(store.read_decisions()) == 1
    assert book.calls == [packets[0]["packet_id"]]


def test_direct_http_submit_rejects_unauth_invalid_and_oversized_requests(tmp_path: Path) -> None:
    _, packets, _, svc = setup_service(tmp_path, count=1)
    token = "x" * 32
    app = build_mcp_app(svc, token=token)
    client = TestClient(app)
    cursor = svc.cursor_for_seq(0)

    payload = {
        "run_id": "direct-http-run-invalid",
        "cursor": cursor,
        "decisions": [
            {
                **decision(packets[0]["packet_id"], "ABSTAIN"),
                "action": "FLIP",
            }
        ],
    }
    assert client.post("/v1/decisions", json=payload).status_code == 401

    headers = {"Authorization": f"Bearer {token}"}
    invalid = client.post("/v1/decisions", headers=headers, json=payload)
    assert invalid.status_code == 400

    oversized = b'{"run_id":"x","cursor":"y","decisions":[],"padding":"' + b"x" * (70 * 1024) + b'"}'
    too_large = client.post("/v1/decisions", headers=headers, content=oversized)
    assert too_large.status_code == 413


def test_direct_http_health_is_public_minimal_and_has_no_market_or_outcome_data(tmp_path: Path) -> None:
    _, _, _, svc = setup_service(tmp_path, count=1)
    app = build_mcp_app(svc, token="x" * 32)
    client = TestClient(app)

    response = client.get("/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "service": "senex-gptrader-direct-http",
        "paper_only": True,
        "simulation_only": True,
        "live": False,
    }
    keys = {str(key).lower() for key in body}
    for forbidden in (
        "outcome",
        "price",
        "pnl",
        "equity",
        "cash",
        "trades",
        "recent_results",
    ):
        assert forbidden not in keys
