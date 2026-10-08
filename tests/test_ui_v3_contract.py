"""ORDER199: focused frontend-only truth and mobile safety smoke checks."""

from pathlib import Path
import shutil
import subprocess

import pytest


FRONTEND = Path(__file__).resolve().parents[1] / "senecio_polymarket" / "frontend"


def test_mobile_safety_and_zoom_contract():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    assert "user-scalable=no" not in html
    assert "maximum-scale=1.0" not in html
    for element_id in (
        "safety-paper", "safety-orders", "safety-data", "safety-edge",
        "ui-last-decision", "ui-raw-conviction",
        "ui-evidence-status", "ui-edge-status",
    ):
        assert html.count(f'id="{element_id}"') == 1
    assert "#safety-strip" in css
    assert ".stat:nth-child(n+4) { display: none; }" not in css


def test_raw_conviction_is_not_probability_or_edge():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "Raw conviction · NOT P(correct)" in html
    assert "RAW CONV · NOT CALIBRATED" in html
    assert "rawConviction + ' RAW'" in app
    assert "raw === '—' ? 'UNKNOWN'" in app
    assert "payload.edge.status" in app
    assert "scientificEdge" in app
    assert "Model-market difference · DIAGNOSTIC" in html


def test_freshness_is_failure_aware_and_ws_is_boolean_gated():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "wsLive = status === 'LIVE_WS' && polymarket.ws_connected === true" in app
    assert "CLOB WS NOT CONNECTED" in app
    for label in ("DATA STALE", "PAPER STALE", "ORDERS STALE", "EDGE STALE"):
        assert label in app
    assert "EDGE UNKNOWN" in html


def test_frontend_js_parses_with_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node not available for JS syntax smoke")
    for file_name in ("app.js", "dashboard_truth.js"):
        result = subprocess.run(
            [node, "--check", str(FRONTEND / file_name)],
            capture_output=True, text=True, check=False, timeout=15,
        )
        assert result.returncode == 0, result.stderr
