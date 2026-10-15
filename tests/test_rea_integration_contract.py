from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "senecio_polymarket" / "backend" / "main_real.py").read_text(encoding="utf-8")
ROUTER = (ROOT / "senecio_polymarket" / "backend" / "binance_public_router.py").read_text(encoding="utf-8")
DOCKER = (ROOT / "Dockerfile").read_text(encoding="utf-8")
HTML = (ROOT / "senecio_polymarket" / "frontend" / "index.html").read_text(encoding="utf-8")
PANEL = (ROOT / "senecio_polymarket" / "frontend" / "binance_public_panel.js").read_text(encoding="utf-8")
DIAGNOSTIC = (ROOT / "senecio_polymarket" / "frontend" / "binance_rea_diagnostic.js").read_text(encoding="utf-8")


def test_real_runtime_mounts_both_public_rea_routes():
    assert "from .binance_public_router import router as binance_public_router" in MAIN
    assert "app.include_router(binance_public_router)" in MAIN
    assert '@router.get("/public_klines")' in ROUTER
    assert '@router.get("/rea_paper_diagnostic")' in ROUTER
    assert "@router.post(" not in ROUTER
    assert "ExecutionEngine(" not in ROUTER


def test_dashboard_exposes_explicit_user_triggered_actions_without_polling():
    assert 'id="binance-public-fetch"' in HTML
    assert 'id="binance-rea-evaluate"' in HTML
    assert 'getElementById("binance-public-fetch")' in PANEL
    assert 'getElementById("binance-rea-evaluate")' in DIAGNOSTIC
    assert 'button.addEventListener("click"' in PANEL
    assert 'button.addEventListener("click"' in DIAGNOSTIC
    assert "setInterval(" not in PANEL
    assert "setInterval(" not in DIAGNOSTIC


def test_runtime_image_copies_complete_deterministic_evaluator_subset():
    for name in ("contracts.py", "accounting.py", "replay.py", "strategies.py", "evaluation.py"):
        assert f"COPY research/rea_engines/{name} ./research/rea_engines/{name}" in DOCKER


def test_diagnostic_declares_no_credentials_accounts_or_order_execution():
    for statement in (
        '"api_key_used": False',
        '"signed_requests_used": False',
        '"account_endpoints_called": False',
        '"order_endpoints_called": 0',
        '"live_orders_submitted": 0',
        "No signal from this diagnostic is routed to ExecutionEngine",
    ):
        assert statement in ROUTER
