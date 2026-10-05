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
    actual = dict(m.FROZEN_PRIMARY)
    actual["market_plus_senex_log_loss"] += 1e-5
    manifest = {"primary": dict(m.FROZEN_PRIMARY)}

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



def test_primary_manifest_guard_rejects_nonfinite_expected():
    m = _load()
    actual = {
        "market_only_brier": m.FROZEN_PRIMARY["market_only_brier"],
        "market_plus_senex_brier": m.FROZEN_PRIMARY["market_plus_senex_brier"],
        "market_only_log_loss": m.FROZEN_PRIMARY["market_only_log_loss"],
        "market_plus_senex_log_loss": m.FROZEN_PRIMARY["market_plus_senex_log_loss"],
    }
    manifest = {"primary": dict(m.FROZEN_PRIMARY)}
    manifest["primary"]["market_only_brier"] = float("nan")

    with pytest.raises(ValueError, match="PRIMARY_REPRO_EXPECTED_NONFINITE"):
        m.assert_primary_reproduction(actual, manifest)


def test_frozen_artifact_guard_rejects_different_bytes(tmp_path):
    m = _load()
    paths = {}
    artifacts = {}
    for key, expected in m.FROZEN_ARTIFACT_SHA256.items():
        path = tmp_path / key
        path.write_bytes(b"not-the-frozen-artifact")
        paths[key] = path
        artifacts[key] = {"sha256": expected}

    with pytest.raises(ValueError, match="FROZEN_ARTIFACT_HASH_MISMATCH"):
        m.assert_frozen_artifact_hashes(paths, {"artifacts": artifacts})


def test_paired_delta_honors_zero_bootstrap():
    m = _load()
    import numpy as np

    labels = np.asarray([0.0, 1.0])
    candidate = np.asarray([0.2, 0.8])
    baseline = np.asarray([0.3, 0.7])

    assert m._paired_delta(
        labels,
        candidate,
        baseline,
        metric="brier",
        replicates=0,
        seed=7,
    ) == {}