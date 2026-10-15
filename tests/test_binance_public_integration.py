from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = ROOT / "senecio_polymarket" / "frontend" / "index.html"
DATA_JS = ROOT / "senecio_polymarket" / "frontend" / "binance_public_panel.js"
REA_JS = ROOT / "senecio_polymarket" / "frontend" / "binance_rea_diagnostic.js"
MAIN = ROOT / "senecio_polymarket" / "backend" / "main.py"
ROUTER = ROOT / "senecio_polymarket" / "backend" / "binance_public_router.py"
DOCKERFILE = ROOT / "Dockerfile"

class BinancePublicIntegrationSurfaceTests(unittest.TestCase):
    def test_dashboard_has_manual_public_data_and_paper_controls(self):
        html = HTML.read_text(encoding="utf-8")
        self.assertEqual(html.count('id="binance-public-panel"'), 1)
        self.assertEqual(html.count('id="binance-public-fetch"'), 1)
        self.assertEqual(html.count('id="binance-rea-evaluate"'), 1)
        self.assertEqual(html.count('id="binance-public-content"'), 1)
        self.assertEqual(html.count('id="binance-rea-content"'), 1)
        self.assertIn('/static/binance_public_panel.js', html)
        self.assertIn('/static/binance_rea_diagnostic.js', html)
        self.assertIn("NO API KEY · NO ORDERS", html)

    def test_frontend_requests_only_after_click_and_renders_untrusted_data_as_text(self):
        data_js = DATA_JS.read_text(encoding="utf-8")
        rea_js = REA_JS.read_text(encoding="utf-8")
        for source in (data_js, rea_js):
            self.assertIn('addEventListener("click"', source)
            self.assertIn('method: "GET"', source)
            self.assertIn("textContent", source)
            self.assertNotIn(".innerHTML", source)
            self.assertNotIn("new WebSocket", source)
            self.assertNotIn("setInterval(", source)
            self.assertNotIn("setTimeout(", source)
            self.assertNotIn("/api/binance/order", source)
            self.assertNotIn("/api/binance/cancel", source)
        self.assertIn("/api/binance/public_klines?", data_js)
        self.assertIn("/api/binance/rea_paper_diagnostic?", rea_js)
        self.assertIn('safe.EDGE !== "UNPROVEN"', rea_js)

    def test_main_mounts_public_data_router_without_editing_execution_modules(self):
        main = MAIN.read_text(encoding="utf-8")
        main_real = (ROOT / "senecio_polymarket" / "backend" / "main_real.py").read_text(encoding="utf-8")
        route = ROUTER.read_text(encoding="utf-8")
        self.assertIn("from .binance_public_router import router as binance_public_router", main)
        self.assertIn("app.include_router(binance_public_router)", main)
        self.assertIn("from .binance_public_router import router as binance_public_router", main_real)
        self.assertIn("app.include_router(binance_public_router)", main_real)
        self.assertIn("public_method_guard", main_real)
        self.assertIn("SAFE_PUBLIC_METHODS", main_real)
        self.assertIn('prefix="/api/binance"', route)
        self.assertIn('"/public_klines"', route)
        self.assertIn('"/rea_paper_diagnostic"', route)
        self.assertIn("SENEX_BINANCE_PUBLIC_DATA_ENABLED", route)
        self.assertIn("order_endpoints_called", route)

    def test_container_copies_only_deterministic_rea_engine_modules_not_order_contract(self):
        docker = DOCKERFILE.read_text(encoding="utf-8")
        for item in ["contracts.py", "accounting.py", "replay.py", "strategies.py", "evaluation.py"]:
            self.assertIn("COPY research/rea_engines/" + item, docker)
        self.assertNotIn("COPY research/rea_engines/paper_execution_contract.py", docker)
        self.assertNotIn("COPY research/rea_engines/data", docker)

if __name__ == "__main__":
    unittest.main()
