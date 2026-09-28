from __future__ import annotations

from pathlib import Path

from senecio_polymarket.backend.gptrader.sealer import PacketSealer
from senecio_polymarket.backend.portfolio.trade_journal import TradeJournal


def _source() -> dict:
    return {
        "timestamp": "2026-09-27T23:30:00+00:00",
        "symbol": "BTCUSDT",
        "prediction": "LONG",
        "confidence": 0.61,
        "ev": 0.01,
        "price_now": 100.0,
        "_audit": {"confidence_semantics_v1": {"semantics": "RAW_CONVICTION"}},
        "outcome": None,
        "price_1h_later": None,
    }


def test_sealer_fsyncs_parent_for_new_log_and_atomic_checkpoint(tmp_path: Path, monkeypatch) -> None:
    import senecio_polymarket.backend.gptrader.sealer as module

    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_fsync_parent",
        lambda path: calls.append(Path(path).name),
        raising=False,
    )
    PacketSealer(root=tmp_path).seal(_source())

    assert "sealed_packets.jsonl" in calls
    assert "packet_seq" in calls


def test_trade_journal_fsyncs_file_and_parent_on_first_record(tmp_path: Path, monkeypatch) -> None:
    import senecio_polymarket.backend.portfolio.trade_journal as module

    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "_fsync_parent",
        lambda path: calls.append(Path(path).name),
        raising=False,
    )
    journal = TradeJournal(path=str(tmp_path / "trades.jsonl"), supabase_mirror=False)
    journal._append({"trade_id": "t1", "realized_pnl_usd": 0.0})

    assert "trades.jsonl" in calls
    assert journal.path.read_text(encoding="utf-8").endswith("\n")
