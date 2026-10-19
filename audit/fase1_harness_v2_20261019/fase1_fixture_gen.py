#!/usr/bin/env python3
"""SENEX Fase 1 — Generador de fixture sintetica (REGENERATED v2).

PROVENANCE: REGENERATED 2026-10-19. This is NOT a byte-copy of the lost v1
fixture (sha256 78cfe41c..., destroyed in the 2026-10-19 sandbox incident).
Protocol identical; bytes and hashes are new. TEST-ONLY synthetic evidence —
never historical, never authoritative, never a substitute for
authority-BTCUSDT.json from the Northflank volume.

Rows satisfy the COMPLETE runtime proof-qualification contract
(backend/settlement_proof.py::is_proof_qualified) so the harness exercises the
real pipeline: origin witness, dual outcomes, aud063-v1 settlement contract,
1m historical price evidence, canonical candle math, settlement observation,
AND directional derivation of outcomes from prices (directional_outcome): the
qualified outcome of a row is exactly the direction-consistent result implied
by origin vs 1h price — same causal rule as the live pipeline. The fixture's
confidence->P(WIN) relationship is the only synthetic element (the signal
being simulated); everything else obeys the runtime contract.

Usage:  python3 fase1_fixture_gen.py [output.jsonl]
Deterministic: fixed seed, fixed epoch start, no wall-clock input.
"""
from __future__ import annotations

import json
import random
import sys
from datetime import datetime, timedelta, timezone

SEED = 20261018
N_ROWS = 300
START = datetime(2026, 9, 19, 0, 0, 0, tzinfo=timezone.utc)
SYMBOL = "BTCUSDT"
EXCHANGE = "okx"
CANDLE_INTERVAL_MS = 60_000
WINDOW_15M_S = 900
WINDOW_1H_S = 3600
BASE_PRICE = 60_000.0

# Live-runtime identity mirrored for the seal selftest (readiness probe
# 2026-10-19: commit 933f0619, tree 99ce7a7e, build digest 9355a157...).
LIVE_SOURCE_COMMIT = "933f0619f8c1e5373877e079528ca0d8a1ca000a"
LIVE_SOURCE_TREE = "99ce7a7e6412eae3afa66a208992abd73e0a1886"
LIVE_BUILD_DIGEST = "sha256:9355a15780f505cfcc9eb6be5d656d31934699b91320305888fec7159046fb7a"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _price_evidence(ts: datetime, window_s: int, price: float) -> dict:
    """Build one aud063-v1 historical-price-evidence block (1m candle math)."""
    target_ms = int((ts + timedelta(seconds=window_s)).timestamp() * 1000)
    open_ms = (target_ms // CANDLE_INTERVAL_MS) * CANDLE_INTERVAL_MS
    close_ms = open_ms + CANDLE_INTERVAL_MS
    observed = ts + timedelta(seconds=window_s + 120)
    return {
        "version": "historical-price-evidence-v1",
        "source": EXCHANGE,
        "symbol": "BTC/USDT",
        "window_seconds": int(window_s),
        "target_epoch_ms": target_ms,
        "candle_open_epoch_ms": open_ms,
        "candle_close_epoch_ms": close_ms,
        "candle_interval_ms": CANDLE_INTERVAL_MS,
        "target_offset_from_candle_open_ms": target_ms - open_ms,
        "price": price,
        "observed_at": _iso(observed),
        "selection_rule": "ONE_MINUTE_CANDLE_CONTAINING_EXACT_TARGET",
        "maturity_rule": "OBSERVED_AT_GTE_CANDLE_CLOSE_EPOCH_MS",
    }


def build_row(i: int, rng: random.Random) -> dict:
    ts = START + timedelta(hours=i)
    direction = rng.choice(("LONG", "SHORT"))
    # ~10% rows without usable confidence (None) -> valid_confidence < n,
    # exercising the harness confidence filter exactly like live data.
    confidence = None if (i % 10 == 7) else round(rng.uniform(0.05, 0.95), 4)
    p_win = 0.49 if confidence is None else 0.35 + 0.30 * confidence
    outcome = "WIN" if rng.random() < p_win else "LOSS"
    price_now = round(BASE_PRICE + i * 7.3 + rng.uniform(-40.0, 40.0), 2)
    # Prices MUST imply the outcome (runtime directional_outcome rule):
    # LONG+WIN or SHORT+LOSS -> 1h price above origin; else below.
    up_1h = (direction == "LONG") == (outcome == "WIN")
    mag_1h = rng.uniform(0.002, 0.009)
    price_1h = round(price_now * (1.0 + (mag_1h if up_1h else -mag_1h)), 2)
    outcome_15m = outcome if rng.random() < 0.8 else ("LOSS" if outcome == "WIN" else "WIN")
    up_15m = (direction == "LONG") == (outcome_15m == "WIN")
    mag_15m = rng.uniform(0.0005, 0.004)
    price_15m = round(price_now * (1.0 + (mag_15m if up_15m else -mag_15m)), 2)
    row = {
        "ts": _iso(ts),
        "id": i + 1,
        "symbol": SYMBOL,
        "prediction": direction,
        "confidence": confidence,
        "ev": 0.0,
        "price_now": price_now,
        "exchange_used": EXCHANGE,
        "outcome": outcome,
        "audit": {
            "origin_price_v1": {
                "version": "origin-price-v1",
                "timestamp": _iso(ts),
                "source": EXCHANGE,
                "price": price_now,
            },
            "outcomes_dual": {
                "primary_window": "1h",
                "settlement_contract_version": "aud063-v1",
                "outcome_15m": outcome_15m,
                "outcome_1h": outcome,
                "price_15m_later": price_15m,
                "price_1h_later": price_1h,
                "price_evidence_v1": {
                    "15m": _price_evidence(ts, WINDOW_15M_S, price_15m),
                    "1h": _price_evidence(ts, WINDOW_1H_S, price_1h),
                },
                "settlement_observation_v1": {
                    "version": "settlement-observation-v1",
                    "observed_at": _iso(ts + timedelta(hours=1, seconds=60)),
                },
            },
        },
    }
    return row


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "fase1_fixture_test.jsonl"
    rng = random.Random(SEED)
    rows = [build_row(i, rng) for i in range(N_ROWS)]
    with open(out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    n_win = sum(1 for r in rows if r["outcome"] == "WIN")
    n_conf = sum(1 for r in rows if r["confidence"] is not None)
    print(f"[fixture-gen REGENERATED v2] wrote {len(rows)} rows -> {out}")
    print(f"  seed={SEED} start={_iso(START)} symbol={SYMBOL} exchange={EXCHANGE}")
    print(f"  WIN={n_win} LOSS={len(rows) - n_win} valid_confidence={n_conf}")
    print("  TEST-ONLY synthetic evidence — not historical, not authoritative.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
