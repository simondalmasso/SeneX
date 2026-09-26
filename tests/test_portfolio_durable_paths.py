from pathlib import Path

from senecio_polymarket.backend.portfolio.trade_journal import TradeJournal
from senecio_polymarket.backend.portfolio.shadow_live import ShadowLive


def test_results_dir_env_makes_default_journals_durable(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    journal = TradeJournal()
    shadow = ShadowLive(config={"fetch_real_book": False})
    assert journal.path == tmp_path / "trades.jsonl"
    assert shadow.path == tmp_path / "shadow_trades.jsonl"
    assert shadow.report_path == tmp_path / "shadow_report.json"


def test_explicit_paths_override_durable_default(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path / "durable"))
    explicit = tmp_path / "explicit.jsonl"
    journal = TradeJournal(path=str(explicit))
    assert journal.path == explicit
