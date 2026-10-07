from __future__ import annotations

import pytest

from senecio_polymarket.oracle import predict_only


def test_log_prediction_propagates_journal_io_failure(tmp_path, monkeypatch):
    def fail_open(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(predict_only, "open", fail_open, raising=False)

    with pytest.raises(OSError, match="disk full"):
        predict_only.log_prediction(
            {"timestamp": "2026-10-07T00:00:00Z", "symbol": "BTCUSDT"},
            str(tmp_path / "predictions.jsonl"),
        )
