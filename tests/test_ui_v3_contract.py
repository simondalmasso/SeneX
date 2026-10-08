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


def test_data_badge_requires_all_domain_health():
    """Execute the actual production badge function against deterministic state."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node not available for data badge behavioral regression")
    script = r"""
const fs = require('fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[1], 'utf8');
const start = source.indexOf('  function syncDataBadge() {');
const end = source.indexOf('  async function getJSON(url)', start);
assert(start >= 0 && end > start);
const factory = new Function('state', 'setSafetyChip',
  source.slice(start, end) + '\nreturn syncDataBadge;');
const cases = [
  [['OK', false], ['OK', false], ['OK', false], 'DATA OK · POLL/REFRESH'],
  [['OK', false], ['ERROR', true], ['OK', false], 'DATA STALE'],
  [['OK', false], ['OK', false], ['ERROR', false], 'DATA UNKNOWN'],
  [['OK', false], ['LOADING', false], ['OK', false], 'DATA UNKNOWN'],
  [['ERROR', false], ['OK', false], ['OK', false], 'DATA UNKNOWN'],
];
for (const statuses of cases) {
  const [context, score, predictions, expected] = statuses;
  const state = {domains:{
    context:{status:context[0], stale:context[1]},
    score:{status:score[0], stale:score[1]},
    predictions:{status:predictions[0], stale:predictions[1]}
  }};
  let badge = null;
  const fn = factory(state, (...args) => { badge = args; });
  fn();
  assert.equal(badge[1], expected);
  assert.equal(badge[3], expected.startsWith('DATA OK'));
}
"""
    result = subprocess.run(
        [node, "-e", script, str(FRONTEND / "app.js")],
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_versioned_frontend_assets_do_not_mix_old_css_and_js():
    """A new HTML release must not reuse cached JS/CSS from an older dashboard."""
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    token = "order199-ui-v3-20261008-r1"
    for asset, attribute in (
        ("styles.css", "href"),
        ("dashboard_truth.js", "src"),
        ("app.js", "src"),
    ):
        assert f'{attribute}="/static/{asset}?v={token}"' in html
        assert f'{attribute}="/static/{asset}"' not in html
