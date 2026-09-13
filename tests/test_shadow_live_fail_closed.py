import asyncio
from unittest.mock import AsyncMock, patch

from senecio_polymarket.backend.portfolio.shadow_live import ShadowLive


def test_shadow_book_failure_never_matches_paper_fill(tmp_path):
    shadow = ShadowLive(config={
        "output_path": str(tmp_path / "shadow.jsonl"),
        "fetch_real_book": True,
    })
    fill = {
        "order_id": "ord-1", "symbol": "BTCUSDT", "side": "BUY",
        "qty": 0.01, "price": 100000.0, "slippage_bps": 0.0,
        "fee_usd": 0.5, "latency_ms": 0, "ts": "2026-09-12T07:00:00+00:00",
    }
    with patch.object(shadow, "_fetch_real_book", new=AsyncMock(return_value={})):
        asyncio.run(shadow._process_fill(fill))

    trade = shadow._trades[-1]
    assert trade.fill_match is False
    assert trade.real_estimated_fill_price == 0.0
    assert trade.real_estimated_fill_qty == 0.0
    assert trade.real_fee_usd == 0.0
