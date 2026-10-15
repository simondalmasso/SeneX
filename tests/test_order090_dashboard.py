from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "senecio_polymarket" / "frontend" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "senecio_polymarket" / "frontend" / "styles.css").read_text(encoding="utf-8")
JS = (ROOT / "senecio_polymarket" / "frontend" / "app.js").read_text(encoding="utf-8")


def test_retired_decision_model_panels_and_routes_are_absent():
    assert "gptrader" not in HTML.lower()
    assert "/api/gptrader/" not in JS
    assert "renderGPTrader" not in JS
    assert "refreshGPTrader" not in JS
    assert "gptrader" not in CSS.lower()


def test_dashboard_grid_names_the_four_information_lanes():
    for area in ("oracle", "execution", "markets", "diagnostics"):
        assert f"grid-area: {area}" in CSS
    assert "grid-template-areas" in CSS
    assert ".col::before" in CSS


def test_dashboard_no_longer_polls_a_retired_decision_model():
    assert "setInterval(refreshGPTrader" not in JS
    assert "getJSON('/api/gptrader" not in JS
    assert "GPTrader PAPER/HYPOTHETICAL" not in HTML


def test_desktop_breakpoint_prevents_four_lane_minimum_width_overflow():
    assert "@media (max-width: 1360px)" in CSS
    assert "overflow-x: hidden" in CSS
