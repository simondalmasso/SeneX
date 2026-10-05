from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research" / "edge" / "order099" / "historical_calibration.py"


def _load():
    spec = importlib.util.spec_from_file_location("order099_historical_calibration", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_primary_manifest_guard_fails_closed_on_metric_mismatch():
    m = _load()
    actual = {
        "market_only_brier": 0.10,
        "market_plus_senex_brier": 0.09,
        "market_only_log_loss": 0.30,
        "market_plus_senex_log_loss": 0.29,
    }
    manifest = {
        "primary": {
            "market_only_brier": 0.10,
            "market_plus_senex_brier": 0.09,
            "market_only_log_loss": 0.30,
            "market_plus_senex_log_loss": 0.31,
        }
    }

    with pytest.raises(ValueError, match="PRIMARY_REPRO_MISMATCH"):
        m.assert_primary_reproduction(actual, manifest)


def test_historical_calibration_module_is_research_only():
    text = MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "httpx",
        "requests.",
        "websockets",
        "oracle_runner",
        "binance_sim_lane",
        "create_order",
        "place_order",
        "order100",
    )
    for token in forbidden:
        assert token not in text

def test_historical_calibration_direct_cli_help():
    completed = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--help"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "--run-manifest" in completed.stdout
