from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_REAL = ROOT / "senecio_polymarket" / "backend" / "main_real.py"
APP_JS = ROOT / "senecio_polymarket" / "frontend" / "app.js"
INDEX_HTML = ROOT / "senecio_polymarket" / "frontend" / "index.html"
ORACLE_RUNNER = ROOT / "senecio_polymarket" / "backend" / "oracle_runner.py"


def test_binance_sim_public_surface_is_get_only():
    text = MAIN_REAL.read_text(encoding="utf-8")
    assert '@app.get("/api/paper/binance-sim/state")' in text
    assert '@app.get("/api/paper/binance-sim/trades")' in text
    for method in ("post", "put", "patch", "delete"):
        assert f'@app.{method}("/api/paper/binance-sim/' not in text


def test_dashboard_has_isolated_simulated_wallet_panel():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'id="binance-sim-panel"' in html
    assert "BINANCE-SIM PAPER — ISOLATED" in html
    assert "18.63631644 USDT" in html
    assert "SIMULATED / PAPER — NOT BINANCE BALANCE" in html
    for element_id in (
        "binance-sim-cash",
        "binance-sim-equity",
        "binance-sim-open",
        "binance-sim-direction",
        "binance-sim-notional",
        "binance-sim-upnl",
        "binance-sim-rpnl",
        "binance-sim-trades-body",
    ):
        assert f'id="{element_id}"' in html


def test_dashboard_polls_binance_sim_state_and_never_claims_real_balance():
    js = APP_JS.read_text(encoding="utf-8")
    assert "refreshBinanceSim" in js
    assert "renderBinanceSim" in js
    assert "getJSON('/api/paper/binance-sim/state')" in js
    assert "NO Binance API / NO real orders" in js
    assert "NOT BINANCE BALANCE" in js


def test_dashboard_javascript_parses_when_node_is_available():
    try:
        result = subprocess.run(
            ["node", "--check", str(APP_JS)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except FileNotFoundError:
        return
    assert result.returncode == 0, result.stderr


def test_binance_sim_uses_canonical_persistent_results_resolver():
    text = ORACLE_RUNNER.read_text(encoding="utf-8")
    assert "from .portfolio.persistence_paths import resolve_path" in text
    assert '"binance_sim_lane.json"' in text
    assert 'env_key="SENEX_BINANCE_SIM_STATE_PATH"' in text
