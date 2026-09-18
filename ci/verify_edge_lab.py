from __future__ import annotations

import re
import subprocess
from pathlib import Path

from senecio_polymarket.backend.paper_lock import safety_projection

ROOT = Path(__file__).resolve().parents[1]


def tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return [ROOT / line for line in result.stdout.splitlines() if line.strip()]


def verify_paper_safety() -> None:
    safety = safety_projection()
    expected = {
        "trade_mode": "PAPER",
        "orders_enabled": False,
        "live_capital_locked": True,
        "hard_paper_lock": True,
    }
    for key, value in expected.items():
        assert safety.get(key) == value, f"PAPER invariant failed: {key}"
    print("PAPER_INVARIANTS=PASS")


def verify_order_capability_boundaries() -> None:
    runtime = (ROOT / "senecio_polymarket/backend/oracle_runner.py").read_text(encoding="utf-8")
    for forbidden in (
        "place_market_order",
        "create_market_order",
        "create_order",
        "fetch_balance",
        "withdraw",
    ):
        assert forbidden not in runtime, f"private order capability entered runtime: {forbidden}"

    connector = (ROOT / "senecio_polymarket/oracle/exchange_connector.py").read_text(encoding="utf-8")
    if ".create_market_order(" in connector:
        for guard in (
            'exchange_name == "binance_testnet"',
            'BINANCE_TESTNET_KEY',
            'BINANCE_TESTNET_SECRET',
            'set_sandbox_mode(True)',
            '"testnet" not in fapi_private',
        ):
            assert guard in connector, f"testnet order guard missing: {guard}"
        assert "BINANCE_KEY" not in connector
        assert "BINANCE_SECRET" not in connector
    print("ORDER_CAPABILITY_BOUNDARY=PASS")


def verify_edge_lab_isolation() -> None:
    production_root = ROOT / "senecio_polymarket"
    hits = []
    for path in production_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "edge_lab" in text:
            hits.append(str(path.relative_to(ROOT)))
    assert not hits, "production runtime imports/references edge_lab: " + ", ".join(hits)
    print("EDGE_LAB_RUNTIME_IMPORTS=0")


def verify_secrets() -> None:
    patterns = {
        "private-key-header": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "gitlab-pat": re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
        "github-pat": re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        "openai-project-key": re.compile(r"sk-proj-[A-Za-z0-9_-]{20,}"),
    }
    hits: list[str] = []
    for path in tracked_files():
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".zip", ".pdf"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, pattern in patterns.items():
            if pattern.search(text):
                hits.append(f"{name}:{path.relative_to(ROOT)}")
    assert not hits, "credential pattern(s) found: " + ", ".join(hits)
    print("SECRET_SCAN=CLEAN")


if __name__ == "__main__":
    verify_paper_safety()
    verify_order_capability_boundaries()
    verify_edge_lab_isolation()
    verify_secrets()
