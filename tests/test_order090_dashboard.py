from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "senecio_polymarket" / "frontend" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "senecio_polymarket" / "frontend" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "senecio_polymarket" / "frontend" / "app.js").read_text(encoding="utf-8")


def test_cockpit_has_explicit_execution_lane_and_trade_tape():
    assert 'id="gptrader-trades-panel"' in HTML
    assert 'id="gptrader-trades-body"' in HTML
    assert "GPTrader PAPER trade tape" in HTML
    assert 'id="gptrader-decision-log"' in HTML
    assert 'id="gptrader-last-run"' in HTML


def test_dashboard_grid_names_the_four_information_lanes():
    for area in ("oracle", "execution", "markets", "diagnostics"):
        assert f"grid-area: {area}" in CSS
    assert "grid-template-areas" in CSS
    assert ".col::before" in CSS


def test_gptrader_trade_tape_is_read_only_and_polling():
    assert "function renderGPTraderTrades" in JS
    assert "function refreshGPTraderTrades" in JS
    assert "getJSON('/api/gptrader/trades?limit=20')" in JS
    assert "setInterval(refreshGPTraderTrades, 5000)" in JS
    assert "fetch('/mcp" not in JS
    assert 'getJSON("/mcp' not in JS


def test_desktop_breakpoint_prevents_four_lane_minimum_width_overflow():
    assert "@media (max-width: 1360px)" in CSS
    assert "overflow-x: hidden" in CSS


def test_trade_tape_null_pnl_stays_unknown():
    assert "row.realized_pnl_usd == null" in JS
    assert 'scope="col"' in HTML
