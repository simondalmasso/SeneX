"""ORDER199 modern dashboard: real observations, safety truth, accessible navigation."""
from pathlib import Path
import shutil
import subprocess

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "senecio_polymarket" / "frontend"


def test_sidebar_destinations_are_existing_readonly_panels():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for anchor in (
        "score-panel", "predictions-panel", "poly-book-panel",
        "poly-panel", "safety-panel", "model-quality-panel",
    ):
        assert f'href="#{anchor}"' in html
        assert html.count(f'id="{anchor}"') == 1
    assert 'id="sidebar"' in html
    assert 'id="safety-strip"' in html


def test_coherent_static_assets_and_mobile_grid():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    css = (FRONTEND / "styles.css").read_text(encoding="utf-8")
    tag = "order199-modern-dashboard-20261008-r2"
    for name, attribute in (
        ("styles.css", "href"),
        ("dashboard_truth.js", "src"),
        ("app.js", "src"),
    ):
        assert f'{attribute}="/static/{name}?v={tag}"' in html
    assert "user-scalable=no" not in html
    assert "#sidebar" in css and "#safety-strip" in css
    assert "@media (max-width:850px)" in css
    assert "grid-template-columns:minmax(0,1fr) !important" in css


def test_chart_uses_only_timestamped_actual_raw_btc_snapshots():
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="raw-history-visual"' in html
    assert "function renderRawHistory(rows)" in app
    assert "renderRawHistory(rows);" in app
    assert "symbolKey(row.symbol) === 'BTCUSDT'" in app
    assert "Date.parse(row.ts || row.created_at || '')" in app
    assert "point.value >= 0 && point.value <= 1" in app
    assert "distinct.length < 2" in app
    assert "STALE · prediction API unavailable" in app
    assert "NOT P(correct)" in html
    assert "EDGE · scientific status" in html


def test_frontend_javascript_syntax():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node unavailable")
    for name in ("app.js", "dashboard_truth.js"):
        result = subprocess.run(
            [node, "--check", str(FRONTEND / name)], capture_output=True,
            text=True, timeout=20, check=False,
        )
        assert result.returncode == 0, result.stderr
