#!/usr/bin/env python3
"""
SENECIO — ORACLE_LAB
=====================

Transforma predictions.jsonl en evidencia estadística.

REGLAS:
    ✅ Medir
    ✅ Clasificar
    ✅ Reportar
    ❌ No modificar el SDC
    ❌ No agregar nuevas señales
    ❌ No optimizar parámetros

Primero medir. Después decidir.

MÉTRICAS:
    - Accuracy, Precision, Recall, F1
    - Brier Score
    - Calibration Error (ECE)
    - Confusion Matrix
    - EV por bucket de confianza
    - EV por régimen
    - EV por símbolo
    - Sharpe teórico de las predicciones
    - Ranking de señales más predictivas
    - Ranking de señales más destructivas
    - Top 20 mejores predicciones
    - Top 20 peores predicciones

SALIDA:
    oracle_report.json
    oracle_report.md

USAGE:
    python3 oracle_lab.py
    python3 oracle_lab.py --path ./senecio_output/predictions.jsonl
    python3 oracle_lab.py --synthetic 100   # Generar dataset sintético para testing
"""

import sys
import os
import json
import math
import random
import argparse
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple
from collections import defaultdict

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
PIPELINE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PIPELINE_DIR)

DEFAULT_PREDICTIONS_PATH = os.path.join(PIPELINE_DIR, "senecio_output", "predictions.jsonl")
DEFAULT_OUTPUT_DIR = os.path.join(PIPELINE_DIR, "senecio_output")


# ═══════════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════════════════

def load_predictions(path: str) -> List[dict]:
    """Load predictions from JSONL file.

    Returns list of prediction dicts. Handles malformed lines gracefully.
    """
    if not os.path.exists(path):
        print(f"[ERROR] No predictions file at {path}")
        return []

    predictions = []
    with open(path, "r") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                pred = json.loads(line)
                pred["_line"] = line_num
                predictions.append(pred)
            except json.JSONDecodeError as e:
                print(f"[WARN] Line {line_num}: malformed JSON — {e}")

    return predictions


# ═══════════════════════════════════════════════════════════════════════════
# SYNTHETIC DATA GENERATOR (for testing the lab itself, NOT for real analysis)
# ═══════════════════════════════════════════════════════════════════════════

def generate_synthetic_predictions(n: int, output_path: str):
    """Generate a realistic synthetic predictions.jsonl for testing ORACLE_LAB.

    This is ONLY for validating the lab's statistical pipeline.
    It mimics the exact structure of real predictions with realistic distributions.
    """
    rng = random.Random(42)  # Deterministic seed
    symbols = ["ETHUSDT", "BTCUSDT", "SOLUSDT", "BNBUSDT"]
    regimes = ["RANGING", "TRENDING", "HIGH_VOL"]
    signal_names = ["orderflow", "volume_delta", "bidask", "funding", "oi", "price_momentum"]

    base_time = datetime(2026, 6, 12, 0, 0, 0, tzinfo=timezone.utc)

    with open(output_path, "w") as f:
        for i in range(n):
            ts = base_time.timestamp() + i * 900  # 15-minute intervals
            ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc)

            # Regime distribution: 50% RANGING, 35% TRENDING, 15% HIGH_VOL
            regime_roll = rng.random()
            regime = "RANGING" if regime_roll < 0.50 else ("TRENDING" if regime_roll < 0.85 else "HIGH_VOL")

            # Symbol distribution
            symbol = rng.choice(symbols)

            # Generate pressures based on regime
            if regime == "TRENDING":
                of = rng.gauss(0.15, 0.3)
                ba = rng.gauss(0.1, 0.2)
                pm = rng.gauss(0.08, 0.15)
            elif regime == "HIGH_VOL":
                of = rng.gauss(0, 0.5)
                ba = rng.gauss(0, 0.4)
                pm = rng.gauss(0, 0.3)
            else:  # RANGING
                of = rng.gauss(0, 0.15)
                ba = rng.gauss(0, 0.1)
                pm = rng.gauss(0, 0.05)

            vol_delta = rng.gauss(0, 0.3)
            fund = rng.gauss(0, 0.005)
            oi = rng.gauss(0, 0.02)

            total_pressure = of + vol_delta * pm + ba + fund + oi + pm

            # Direction from total_pressure
            if total_pressure > 0.05:
                direction = "LONG"
            elif total_pressure < -0.05:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"

            # Confidence based on |total_pressure| + noise
            raw_conf = min(1.0, abs(total_pressure) * 2.0 + rng.gauss(0, 0.1))
            confidence = max(0.0, min(1.0, raw_conf))

            # Noise inversely correlated with confidence
            noise = max(0.05, 1.0 - confidence + rng.gauss(0, 0.1))

            # Prediction
            if direction == "NEUTRAL" or confidence < 0.55:
                prediction = "FLAT"
            else:
                prediction = direction

            # EV
            ev = confidence * rng.gauss(0.003, 0.002) if prediction != "FLAT" else rng.gauss(0, 0.0001)

            # Price
            base_prices = {"ETHUSDT": 2700, "BTCUSDT": 105000, "SOLUSDT": 170, "BNBUSDT": 700}
            price_now = base_prices.get(symbol, 1000) * (1 + rng.gauss(0, 0.02))

            # Outcome: simulate 15-minute move
            if prediction == "LONG":
                # Skill edge: ~54% accuracy when confident
                move_pct = rng.gauss(0.001, 0.005) * (1 + confidence)
                price_15m = price_now * (1 + move_pct)
                outcome = "CORRECT" if move_pct > 0 else "WRONG"
            elif prediction == "SHORT":
                move_pct = rng.gauss(-0.001, 0.005) * (1 + confidence)
                price_15m = price_now * (1 + move_pct)
                outcome = "CORRECT" if move_pct < 0 else "WRONG"
            else:
                move_pct = rng.gauss(0, 0.003)
                price_15m = price_now * (1 + move_pct)
                outcome = "SKIP"

            # Build pressures dict
            pressures = {
                "orderflow": round(of, 6),
                "volume_delta": round(vol_delta * pm, 6),
                "bidask": round(ba, 6),
                "funding": round(fund, 6),
                "oi": round(oi, 6),
                "price_momentum": round(pm, 6),
            }

            pred = {
                "timestamp": ts_dt.isoformat(),
                "symbol": symbol,
                "prediction": prediction,
                "confidence": round(confidence, 4),
                "ev": round(ev, 8),
                "price_now": round(price_now, 2),
                "price_15m_later": round(price_15m, 2),
                "outcome": outcome,
                "_audit": {
                    "action_vector": {
                        "action": "EXECUTE" if prediction != "FLAT" else "HOLD",
                        "side": prediction if prediction in ("LONG", "SHORT") else None,
                        "size": round(confidence * 0.15, 6),
                        "reason": f"synthetic_{regime}",
                    },
                    "pipeline": {
                        "step1_market": {
                            "price": round(price_now, 2),
                            "price_momentum": round(pm, 6),
                            "volume_delta": round(vol_delta, 6),
                            "bidask_imbalance": round(ba, 6),
                            "orderflow": round(of, 6),
                            "funding_signal": round(fund, 6),
                            "oi_momentum": round(oi, 6),
                            "spread_pct": round(rng.gauss(0.0001, 0.0002), 8),
                            "volatility": round(rng.gauss(0.015, 0.01), 6),
                            "liquidity_quality": round(rng.gauss(0.95, 0.05), 4),
                        },
                        "step2_features": {
                            "direction": direction,
                            "conviction": round(confidence, 6),
                            "noise": round(noise, 6),
                            "regime_hint": regime,
                            "total_pressure": round(total_pressure, 6),
                            "up_prob": round(max(0, min(1, 0.5 + total_pressure)), 6),
                            "down_prob": round(max(0, min(1, 0.5 - total_pressure)), 6),
                            "agreement": round(rng.gauss(0.6, 0.15), 6),
                            "pressures": pressures,
                        },
                        "step3_risk": {
                            "risk_score": round(rng.gauss(0.1, 0.05), 6),
                            "size_multiplier": round(rng.gauss(0.7, 0.1), 6),
                            "verdict": "ALLOW",
                            "zone": "SAFE",
                        },
                        "step4_ev": {
                            "base_ev": round(ev * 1.5, 8),
                            "adjusted_ev": round(ev, 8),
                            "tradeable": ev > 0,
                            "p_win": round(max(0.5, min(1.0, 0.5 + confidence * 0.3)), 6),
                            "avg_win": round(rng.gauss(0.005, 0.002), 6),
                            "avg_loss": round(rng.gauss(0.004, 0.002), 6),
                        },
                        "step5_feasibility": {
                            "feasible": True,
                            "reason": "execution_feasible",
                            "size_adjustment": round(rng.gauss(0.9, 0.05), 6),
                        },
                    },
                },
            }

            f.write(json.dumps(pred, default=str) + "\n")

    print(f"[SYNTHETIC] Generated {n} predictions → {output_path}")


# ═══════════════════════════════════════════════════════════════════════════
# STATISTICAL ANALYSIS ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def compute_classification_metrics(predictions: List[dict]) -> dict:
    """Compute Accuracy, Precision, Recall, F1, Confusion Matrix.

    Only considers directional predictions (LONG/SHORT) with verified outcomes.
    FLAT predictions are excluded from directional classification metrics.
    """
    # Filter to directional predictions with verified outcomes
    directional = [p for p in predictions
                   if p.get("prediction") in ("LONG", "SHORT")
                   and p.get("outcome") in ("CORRECT", "WRONG")]

    if not directional:
        return {
            "n_directional": 0,
            "accuracy": None,
            "precision_long": None,
            "precision_short": None,
            "recall_long": None,
            "recall_short": None,
            "f1_long": None,
            "f1_short": None,
            "confusion_matrix": None,
            "note": "No verified directional predictions available",
        }

    # Build confusion matrix
    # Rows = actual direction (market went UP or DOWN)
    # Cols = predicted direction (LONG or SHORT)
    # CORRECT LONG = predicted LONG + market went UP
    # CORRECT SHORT = predicted SHORT + market went DOWN
    tp_long = 0    # predicted LONG, market UP (correct)
    fn_long = 0    # predicted SHORT, market UP (missed long)
    tp_short = 0   # predicted SHORT, market DOWN (correct)
    fn_short = 0   # predicted LONG, market DOWN (missed short)

    for p in directional:
        pred = p["prediction"]
        outcome = p["outcome"]

        if pred == "LONG":
            if outcome == "CORRECT":
                tp_long += 1
            else:
                fn_short += 1  # predicted long but market went down
        elif pred == "SHORT":
            if outcome == "CORRECT":
                tp_short += 1
            else:
                fn_long += 1  # predicted short but market went up

    fp_long = fn_short   # predicted long but wrong = false positive for long
    fp_short = fn_long   # predicted short but wrong = false positive for short

    # Accuracy
    total = len(directional)
    correct = tp_long + tp_short
    accuracy = correct / total if total > 0 else 0.0

    # Precision: TP / (TP + FP)
    precision_long = tp_long / (tp_long + fp_long) if (tp_long + fp_long) > 0 else 0.0
    precision_short = tp_short / (tp_short + fp_short) if (tp_short + fp_short) > 0 else 0.0

    # Recall: TP / (TP + FN)
    recall_long = tp_long / (tp_long + fn_long) if (tp_long + fn_long) > 0 else 0.0
    recall_short = tp_short / (tp_short + fn_short) if (tp_short + fn_short) > 0 else 0.0

    # F1
    f1_long = 2 * precision_long * recall_long / (precision_long + recall_long) if (precision_long + recall_long) > 0 else 0.0
    f1_short = 2 * precision_short * recall_short / (precision_short + recall_short) if (precision_short + recall_short) > 0 else 0.0

    return {
        "n_directional": total,
        "accuracy": round(accuracy, 6),
        "precision_long": round(precision_long, 6),
        "precision_short": round(precision_short, 6),
        "recall_long": round(recall_long, 6),
        "recall_short": round(recall_short, 6),
        "f1_long": round(f1_long, 6),
        "f1_short": round(f1_short, 6),
        "confusion_matrix": {
            "predicted_long_actual_up": tp_long,
            "predicted_long_actual_down": fp_long,
            "predicted_short_actual_down": tp_short,
            "predicted_short_actual_up": fp_short,
        },
        "counts": {
            "tp_long": tp_long, "fp_long": fp_long,
            "tp_short": tp_short, "fp_short": fp_short,
            "fn_long": fn_long, "fn_short": fn_short,
        },
    }


def compute_brier_score(predictions: List[dict]) -> Optional[float]:
    """Compute Brier Score for probabilistic predictions.

    Brier = (1/N) * Σ (p_predicted - outcome_binary)²

    For LONG: p_predicted = confidence, outcome = 1 if CORRECT else 0
    For SHORT: p_predicted = confidence, outcome = 1 if CORRECT else 0
    For FLAT: excluded (no directional probability to evaluate)

    Lower is better (0 = perfect calibration, 0.25 = random, 1 = worst).
    """
    directional = [p for p in predictions
                   if p.get("prediction") in ("LONG", "SHORT")
                   and p.get("outcome") in ("CORRECT", "WRONG")]

    if not directional:
        return None

    brier_sum = 0.0
    for p in directional:
        confidence = p.get("confidence", 0.5)
        outcome_binary = 1.0 if p["outcome"] == "CORRECT" else 0.0
        brier_sum += (confidence - outcome_binary) ** 2

    return round(brier_sum / len(directional), 6)


def compute_ece(predictions: List[dict], n_bins: int = 10) -> dict:
    """Compute Expected Calibration Error (ECE).

    ECE = Σ (n_bin / N) * |accuracy_bin - avg_confidence_bin|

    Lower is better (0 = perfectly calibrated).
    """
    directional = [p for p in predictions
                   if p.get("prediction") in ("LONG", "SHORT")
                   and p.get("outcome") in ("CORRECT", "WRONG")]

    if not directional:
        return {"ece": None, "bins": [], "note": "No verified directional predictions"}

    # Create bins
    bin_size = 1.0 / n_bins
    bins = []
    for i in range(n_bins):
        lo = i * bin_size
        hi = (i + 1) * bin_size
        bin_preds = [p for p in directional if lo <= p.get("confidence", 0) < hi]
        if bin_preds:
            avg_conf = sum(p.get("confidence", 0) for p in bin_preds) / len(bin_preds)
            accuracy = sum(1 for p in bin_preds if p["outcome"] == "CORRECT") / len(bin_preds)
            bins.append({
                "range": f"[{lo:.2f}, {hi:.2f})",
                "count": len(bin_preds),
                "avg_confidence": round(avg_conf, 4),
                "accuracy": round(accuracy, 4),
                "gap": round(abs(avg_conf - accuracy), 4),
            })

    # ECE
    total = len(directional)
    ece = sum(b["count"] / total * b["gap"] for b in bins) if total > 0 else 0.0

    return {
        "ece": round(ece, 6),
        "n_bins_with_data": len(bins),
        "bins": bins,
    }


def compute_ev_by_confidence_bucket(predictions: List[dict]) -> dict:
    """Compute average realized EV by confidence bucket.

    Realized EV = actual price change in predicted direction.
    """
    verified = [p for p in predictions
                if p.get("prediction") in ("LONG", "SHORT")
                and p.get("outcome") in ("CORRECT", "WRONG")
                and p.get("price_now", 0) > 0
                and p.get("price_15m_later", 0) > 0]

    if not verified:
        return {"buckets": [], "note": "No verified predictions with price data"}

    # Define buckets
    bucket_defs = [
        ("0.00-0.30", 0.0, 0.30),
        ("0.30-0.50", 0.30, 0.50),
        ("0.50-0.60", 0.50, 0.60),
        ("0.60-0.70", 0.60, 0.70),
        ("0.70-0.80", 0.70, 0.80),
        ("0.80-0.90", 0.80, 0.90),
        ("0.90-1.00", 0.90, 1.00),
    ]

    buckets = []
    for name, lo, hi in bucket_defs:
        bucket_preds = [p for p in verified if lo <= p.get("confidence", 0) < hi]
        if not bucket_preds:
            continue

        realized_evs = []
        for p in bucket_preds:
            price_change = (p["price_15m_later"] - p["price_now"]) / p["price_now"]
            if p["prediction"] == "LONG":
                realized_evs.append(price_change)
            else:  # SHORT
                realized_evs.append(-price_change)

        avg_realized = sum(realized_evs) / len(realized_evs)
        avg_model_ev = sum(p.get("ev", 0) for p in bucket_preds) / len(bucket_preds)
        win_rate = sum(1 for ev in realized_evs if ev > 0) / len(realized_evs)

        buckets.append({
            "bucket": name,
            "count": len(bucket_preds),
            "avg_model_ev": round(avg_model_ev, 8),
            "avg_realized_ev": round(avg_realized, 8),
            "win_rate": round(win_rate, 4),
        })

    return {"buckets": buckets}


def compute_ev_by_regime(predictions: List[dict]) -> dict:
    """Compute average realized EV by market regime."""
    verified = [p for p in predictions
                if p.get("prediction") in ("LONG", "SHORT")
                and p.get("outcome") in ("CORRECT", "WRONG")
                and p.get("price_now", 0) > 0
                and p.get("price_15m_later", 0) > 0]

    if not verified:
        return {"regimes": [], "note": "No verified predictions with price data"}

    # Group by regime from pipeline
    regime_groups = defaultdict(list)
    for p in verified:
        regime = "UNKNOWN"
        audit = p.get("_audit", {})
        pipeline = audit.get("pipeline", {})
        step2 = pipeline.get("step2_features", {})
        if isinstance(step2, dict):
            regime = step2.get("regime_hint", "UNKNOWN")
        regime_groups[regime].append(p)

    regimes = []
    for regime, preds in sorted(regime_groups.items()):
        realized_evs = []
        for p in preds:
            price_change = (p["price_15m_later"] - p["price_now"]) / p["price_now"]
            if p["prediction"] == "LONG":
                realized_evs.append(price_change)
            else:
                realized_evs.append(-price_change)

        avg_realized = sum(realized_evs) / len(realized_evs)
        avg_model_ev = sum(p.get("ev", 0) for p in preds) / len(preds)
        win_rate = sum(1 for ev in realized_evs if ev > 0) / len(realized_evs)

        regimes.append({
            "regime": regime,
            "count": len(preds),
            "avg_model_ev": round(avg_model_ev, 8),
            "avg_realized_ev": round(avg_realized, 8),
            "win_rate": round(win_rate, 4),
        })

    return {"regimes": regimes}


def compute_ev_by_symbol(predictions: List[dict]) -> dict:
    """Compute average realized EV by symbol."""
    verified = [p for p in predictions
                if p.get("prediction") in ("LONG", "SHORT")
                and p.get("outcome") in ("CORRECT", "WRONG")
                and p.get("price_now", 0) > 0
                and p.get("price_15m_later", 0) > 0]

    if not verified:
        return {"symbols": [], "note": "No verified predictions with price data"}

    symbol_groups = defaultdict(list)
    for p in verified:
        symbol_groups[p.get("symbol", "UNKNOWN")].append(p)

    symbols = []
    for symbol, preds in sorted(symbol_groups.items()):
        realized_evs = []
        for p in preds:
            price_change = (p["price_15m_later"] - p["price_now"]) / p["price_now"]
            if p["prediction"] == "LONG":
                realized_evs.append(price_change)
            else:
                realized_evs.append(-price_change)

        avg_realized = sum(realized_evs) / len(realized_evs)
        avg_model_ev = sum(p.get("ev", 0) for p in preds) / len(preds)
        win_rate = sum(1 for ev in realized_evs if ev > 0) / len(realized_evs)

        symbols.append({
            "symbol": symbol,
            "count": len(preds),
            "avg_model_ev": round(avg_model_ev, 8),
            "avg_realized_ev": round(avg_realized, 8),
            "win_rate": round(win_rate, 4),
        })

    return {"symbols": symbols}


def compute_sharpe(predictions: List[dict]) -> dict:
    """Compute theoretical Sharpe ratio of predictions.

    Sharpe = mean(return) / std(return) * sqrt(cycles_per_year)

    Assumes 4 cycles per hour * 24 * 365 = 35040 cycles/year.
    Uses realized returns from directional predictions only.
    """
    verified = [p for p in predictions
                if p.get("prediction") in ("LONG", "SHORT")
                and p.get("outcome") in ("CORRECT", "WRONG")
                and p.get("price_now", 0) > 0
                and p.get("price_15m_later", 0) > 0]

    if len(verified) < 2:
        return {"sharpe": None, "note": "Need at least 2 verified predictions"}

    returns = []
    for p in verified:
        price_change = (p["price_15m_later"] - p["price_now"]) / p["price_now"]
        if p["prediction"] == "LONG":
            returns.append(price_change)
        else:
            returns.append(-price_change)

    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    std_ret = math.sqrt(variance) if variance > 0 else 0.0001

    # Annualize: 4 cycles/hour * 24 * 365
    cycles_per_year = 35040
    sharpe = (mean_ret / std_ret) * math.sqrt(cycles_per_year)

    return {
        "sharpe": round(sharpe, 4),
        "mean_return": round(mean_ret, 8),
        "std_return": round(std_ret, 8),
        "n_returns": len(returns),
        "cycles_per_year": cycles_per_year,
    }


def compute_signal_rankings(predictions: List[dict]) -> dict:
    """Rank signals by predictive power and destructiveness.

    For each signal (orderflow, volume_delta, bidask, funding, oi, price_momentum):
    - When the signal agreed with the final prediction direction, was the outcome correct?
    - When the signal disagreed, was the outcome wrong?

    Most predictive: high agreement + high accuracy when agreed
    Most destructive: high agreement + low accuracy when agreed (led us astray)
    """
    verified = [p for p in predictions
                if p.get("prediction") in ("LONG", "SHORT")
                and p.get("outcome") in ("CORRECT", "WRONG")]

    if not verified:
        return {"most_predictive": [], "most_destructive": [], "note": "No verified predictions"}

    signal_stats = defaultdict(lambda: {
        "agreed_correct": 0,
        "agreed_wrong": 0,
        "disagreed_correct": 0,
        "disagreed_wrong": 0,
        "total_influence": 0.0,
    })

    for p in verified:
        pred_direction = p["prediction"]
        outcome = p["outcome"]
        is_correct = outcome == "CORRECT"

        audit = p.get("_audit", {})
        pipeline = audit.get("pipeline", {})
        step2 = pipeline.get("step2_features", {})
        pressures = step2.get("pressures", {}) if isinstance(step2, dict) else {}

        if not pressures:
            continue

        for signal_name, signal_value in pressures.items():
            if signal_value == 0:
                continue

            # Signal agreed with prediction direction?
            agreed = (
                (pred_direction == "LONG" and signal_value > 0) or
                (pred_direction == "SHORT" and signal_value < 0)
            )

            stats = signal_stats[signal_name]
            stats["total_influence"] += abs(signal_value)

            if agreed:
                if is_correct:
                    stats["agreed_correct"] += 1
                else:
                    stats["agreed_wrong"] += 1
            else:
                if is_correct:
                    stats["disagreed_correct"] += 1
                else:
                    stats["disagreed_wrong"] += 1

    # Compute accuracy when agreed and disagreed for each signal
    signal_results = []
    for name, stats in signal_stats.items():
        agreed_total = stats["agreed_correct"] + stats["agreed_wrong"]
        disagreed_total = stats["disagreed_correct"] + stats["disagreed_wrong"]
        total = agreed_total + disagreed_total

        if total == 0:
            continue

        agreed_accuracy = stats["agreed_correct"] / agreed_total if agreed_total > 0 else 0
        disagreed_accuracy = stats["disagreed_correct"] / disagreed_total if disagreed_total > 0 else 0

        # Predictive power: how often the signal's agreement correlates with correct outcomes
        # Weighted by how often the signal is influential (total_influence)
        predictive_score = agreed_accuracy * (stats["total_influence"] / max(1, total))

        # Destructive score: how often the signal's agreement led to wrong outcomes
        destructive_score = (1 - agreed_accuracy) * (stats["total_influence"] / max(1, total))

        signal_results.append({
            "signal": name,
            "agreed_total": agreed_total,
            "agreed_accuracy": round(agreed_accuracy, 4),
            "disagreed_total": disagreed_total,
            "disagreed_accuracy": round(disagreed_accuracy, 4),
            "total_influence": round(stats["total_influence"], 4),
            "predictive_score": round(predictive_score, 6),
            "destructive_score": round(destructive_score, 6),
        })

    # Sort by predictive score (descending)
    most_predictive = sorted(signal_results, key=lambda x: x["predictive_score"], reverse=True)
    # Sort by destructive score (descending)
    most_destructive = sorted(signal_results, key=lambda x: x["destructive_score"], reverse=True)

    return {
        "most_predictive": most_predictive,
        "most_destructive": most_destructive,
    }


def compute_top_predictions(predictions: List[dict], n: int = 20) -> dict:
    """Compute top N best and worst predictions.

    Ranked by realized return (best = highest, worst = lowest).
    """
    verified = [p for p in predictions
                if p.get("prediction") in ("LONG", "SHORT")
                and p.get("outcome") in ("CORRECT", "WRONG")
                and p.get("price_now", 0) > 0
                and p.get("price_15m_later", 0) > 0]

    # Compute realized return for each
    with_returns = []
    for p in verified:
        price_change = (p["price_15m_later"] - p["price_now"]) / p["price_now"]
        if p["prediction"] == "LONG":
            realized = price_change
        else:
            realized = -price_change

        with_returns.append({
            "timestamp": p["timestamp"],
            "symbol": p["symbol"],
            "prediction": p["prediction"],
            "confidence": p.get("confidence", 0),
            "ev": p.get("ev", 0),
            "price_now": p["price_now"],
            "price_15m_later": p["price_15m_later"],
            "outcome": p["outcome"],
            "realized_return": round(realized, 6),
        })

    # Sort by realized return
    sorted_by_return = sorted(with_returns, key=lambda x: x["realized_return"], reverse=True)

    best = sorted_by_return[:n]
    worst = sorted_by_return[-n:] if len(sorted_by_return) > n else sorted_by_return
    worst = list(reversed(worst))

    return {
        "top_best": best,
        "top_worst": worst,
    }


# ═══════════════════════════════════════════════════════════════════════════
# REPORT GENERATION
# ═══════════════════════════════════════════════════════════════════════════

def generate_report(predictions: List[dict], output_dir: str) -> dict:
    """Generate the full ORACLE_LAB report.

    Returns the report dict. Also writes oracle_report.json and oracle_report.md.
    """
    total = len(predictions)
    directional = [p for p in predictions if p.get("prediction") in ("LONG", "SHORT")]
    flat = [p for p in predictions if p.get("prediction") == "FLAT"]
    verified = [p for p in predictions if p.get("outcome") in ("CORRECT", "WRONG")]
    verified_directional = [p for p in directional if p.get("outcome") in ("CORRECT", "WRONG")]

    # ── Compute all metrics ──
    classification = compute_classification_metrics(predictions)
    brier = compute_brier_score(predictions)
    ece = compute_ece(predictions)
    ev_confidence = compute_ev_by_confidence_bucket(predictions)
    ev_regime = compute_ev_by_regime(predictions)
    ev_symbol = compute_ev_by_symbol(predictions)
    sharpe = compute_sharpe(predictions)
    signal_ranking = compute_signal_rankings(predictions)
    top_preds = compute_top_predictions(predictions)

    # ── Assemble report ──
    report = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "predictions.jsonl",
            "total_predictions": total,
            "directional": len(directional),
            "flat": len(flat),
            "verified": len(verified),
            "verified_directional": len(verified_directional),
        },
        "classification": classification,
        "probabilistic": {
            "brier_score": brier,
            "ece": ece,
        },
        "ev_analysis": {
            "by_confidence_bucket": ev_confidence,
            "by_regime": ev_regime,
            "by_symbol": ev_symbol,
        },
        "sharpe": sharpe,
        "signal_rankings": signal_ranking,
        "top_predictions": top_preds,
    }

    # ── Write JSON report ──
    os.makedirs(output_dir, exist_ok=True)
    json_path = os.path.join(output_dir, "oracle_report.json")
    with open(json_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"[JSON] {json_path}")

    # ── Write Markdown report ──
    md_path = os.path.join(output_dir, "oracle_report.md")
    md = _build_markdown(report)
    with open(md_path, "w") as f:
        f.write(md)
    print(f"[MD]   {md_path}")

    return report


def _build_markdown(report: dict) -> str:
    """Build the Markdown report from the report dict."""
    lines = []

    meta = report["meta"]
    cls = report["classification"]
    prob = report["probabilistic"]
    ev = report["ev_analysis"]
    sharpe = report["sharpe"]
    signals = report["signal_rankings"]
    top = report["top_predictions"]

    # ── Header ──
    lines.append("# SENECIO ORACLE_LAB — Statistical Evidence Report")
    lines.append("")
    lines.append(f"**Generated:** {meta['generated_at']}")
    lines.append(f"**Source:** {meta['source']}")
    lines.append("")

    # ── Dataset Overview ──
    lines.append("## Dataset Overview")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Predictions | {meta['total_predictions']} |")
    lines.append(f"| Directional (LONG/SHORT) | {meta['directional']} |")
    lines.append(f"| FLAT | {meta['flat']} |")
    lines.append(f"| Verified (outcome known) | {meta['verified']} |")
    lines.append(f"| Verified Directional | {meta['verified_directional']} |")
    lines.append("")

    # ── Classification Metrics ──
    lines.append("## Classification Metrics")
    lines.append("")
    if cls.get("accuracy") is not None:
        lines.append(f"| Metric | LONG | SHORT | Overall |")
        lines.append(f"|--------|------|--------|---------|")
        lines.append(f"| Precision | {cls['precision_long']:.4f} | {cls['precision_short']:.4f} | — |")
        lines.append(f"| Recall | {cls['recall_long']:.4f} | {cls['recall_short']:.4f} | — |")
        lines.append(f"| F1 | {cls['f1_long']:.4f} | {cls['f1_short']:.4f} | — |")
        lines.append(f"| **Accuracy** | — | — | **{cls['accuracy']:.4f}** |")
        lines.append("")

        # Confusion Matrix
        cm = cls.get("confusion_matrix", {})
        if cm:
            lines.append("### Confusion Matrix")
            lines.append("")
            lines.append("| | Market UP | Market DOWN |")
            lines.append("|-----------|-----------|-------------|")
            lines.append(f"| **Predicted LONG** | {cm.get('predicted_long_actual_up', 0)} | {cm.get('predicted_long_actual_down', 0)} |")
            lines.append(f"| **Predicted SHORT** | {cm.get('predicted_short_actual_up', 0)} | {cm.get('predicted_short_actual_down', 0)} |")
            lines.append("")
    else:
        lines.append(f"*{cls.get('note', 'No data')}*")
        lines.append("")

    # ── Probabilistic Calibration ──
    lines.append("## Probabilistic Calibration")
    lines.append("")
    brier = prob.get("brier_score")
    ece_data = prob.get("ece", {})

    if brier is not None:
        lines.append(f"| Metric | Value | Interpretation |")
        lines.append(f"|--------|-------|----------------|")
        lines.append(f"| Brier Score | {brier:.6f} | 0=perfect, 0.25=random, 1=worst |")
        lines.append(f"| ECE | {ece_data.get('ece', 'N/A')} | 0=perfectly calibrated |")
        lines.append("")

        # Calibration bins
        bins = ece_data.get("bins", [])
        if bins:
            lines.append("### Calibration Bins")
            lines.append("")
            lines.append("| Confidence Range | Count | Avg Confidence | Actual Accuracy | Gap |")
            lines.append("|-----------------|-------|---------------|-----------------|-----|")
            for b in bins:
                gap_indicator = "⚠️" if b["gap"] > 0.1 else "✅"
                lines.append(f"| {b['range']} | {b['count']} | {b['avg_confidence']:.4f} | {b['accuracy']:.4f} | {b['gap']:.4f} {gap_indicator} |")
            lines.append("")
    else:
        lines.append("*Insufficient verified data for calibration analysis*")
        lines.append("")

    # ── EV Analysis ──
    lines.append("## Expected Value Analysis")
    lines.append("")

    # By confidence bucket
    conf_buckets = ev.get("by_confidence_bucket", {}).get("buckets", [])
    if conf_buckets:
        lines.append("### EV by Confidence Bucket")
        lines.append("")
        lines.append("| Bucket | Count | Model EV | Realized EV | Win Rate |")
        lines.append("|--------|-------|----------|-------------|----------|")
        for b in conf_buckets:
            lines.append(f"| {b['bucket']} | {b['count']} | {b['avg_model_ev']:.8f} | {b['avg_realized_ev']:.8f} | {b['win_rate']:.2%} |")
        lines.append("")

    # By regime
    regimes = ev.get("by_regime", {}).get("regimes", [])
    if regimes:
        lines.append("### EV by Market Regime")
        lines.append("")
        lines.append("| Regime | Count | Model EV | Realized EV | Win Rate |")
        lines.append("|--------|-------|----------|-------------|----------|")
        for r in regimes:
            lines.append(f"| {r['regime']} | {r['count']} | {r['avg_model_ev']:.8f} | {r['avg_realized_ev']:.8f} | {r['win_rate']:.2%} |")
        lines.append("")

    # By symbol
    symbols = ev.get("by_symbol", {}).get("symbols", [])
    if symbols:
        lines.append("### EV by Symbol")
        lines.append("")
        lines.append("| Symbol | Count | Model EV | Realized EV | Win Rate |")
        lines.append("|--------|-------|----------|-------------|----------|")
        for s in symbols:
            lines.append(f"| {s['symbol']} | {s['count']} | {s['avg_model_ev']:.8f} | {s['avg_realized_ev']:.8f} | {s['win_rate']:.2%} |")
        lines.append("")

    # ── Sharpe ──
    lines.append("## Theoretical Sharpe Ratio")
    lines.append("")
    if sharpe.get("sharpe") is not None:
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Sharpe (annualized) | {sharpe['sharpe']:.4f} |")
        lines.append(f"| Mean Return | {sharpe['mean_return']:.8f} |")
        lines.append(f"| Std Return | {sharpe['std_return']:.8f} |")
        lines.append(f"| N Returns | {sharpe['n_returns']} |")
        lines.append(f"| Cycles/Year | {sharpe['cycles_per_year']} |")
        lines.append("")
    else:
        lines.append("*Insufficient data for Sharpe calculation*")
        lines.append("")

    # ── Signal Rankings ──
    lines.append("## Signal Rankings")
    lines.append("")

    most_pred = signals.get("most_predictive", [])
    most_dest = signals.get("most_destructive", [])

    if most_pred:
        lines.append("### Most Predictive Signals (agreement → correct outcome)")
        lines.append("")
        lines.append("| Rank | Signal | Agreed Accuracy | Total Influence | Predictive Score |")
        lines.append("|------|--------|----------------|-----------------|------------------|")
        for i, s in enumerate(most_pred, 1):
            lines.append(f"| {i} | {s['signal']} | {s['agreed_accuracy']:.2%} | {s['total_influence']:.4f} | {s['predictive_score']:.6f} |")
        lines.append("")

    if most_dest:
        lines.append("### Most Destructive Signals (agreement → wrong outcome)")
        lines.append("")
        lines.append("| Rank | Signal | Agreed Failure Rate | Total Influence | Destructive Score |")
        lines.append("|------|--------|---------------------|-----------------|-------------------|")
        for i, s in enumerate(most_dest, 1):
            failure_rate = 1.0 - s["agreed_accuracy"]
            lines.append(f"| {i} | {s['signal']} | {failure_rate:.2%} | {s['total_influence']:.4f} | {s['destructive_score']:.6f} |")
        lines.append("")

    # ── Top Predictions ──
    top_best = top.get("top_best", [])
    top_worst = top.get("top_worst", [])

    if top_best:
        lines.append("## Top 20 Best Predictions")
        lines.append("")
        lines.append("| # | Timestamp | Symbol | Dir | Conf | Realized Return |")
        lines.append("|---|-----------|--------|-----|------|----------------|")
        for i, p in enumerate(top_best, 1):
            lines.append(f"| {i} | {p['timestamp'][:19]} | {p['symbol']} | {p['prediction']} | {p['confidence']:.4f} | {p['realized_return']:+.6f} |")
        lines.append("")

    if top_worst:
        lines.append("## Top 20 Worst Predictions")
        lines.append("")
        lines.append("| # | Timestamp | Symbol | Dir | Conf | Realized Return |")
        lines.append("|---|-----------|--------|-----|------|----------------|")
        for i, p in enumerate(top_worst, 1):
            lines.append(f"| {i} | {p['timestamp'][:19]} | {p['symbol']} | {p['prediction']} | {p['confidence']:.4f} | {p['realized_return']:+.6f} |")
        lines.append("")

    # ── Footer ──
    lines.append("---")
    lines.append("")
    lines.append("*SENECIO ORACLE_LAB — Primero medir. Después decidir.*")
    lines.append("")
    lines.append("Rules: No modificar el SDC. No agregar nuevas señales. No optimizar parámetros.")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="SENECIO ORACLE_LAB — Transform predictions into statistical evidence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--path", type=str, default=DEFAULT_PREDICTIONS_PATH,
                        help=f"Predictions JSONL path (default: {DEFAULT_PREDICTIONS_PATH})")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR,
                        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})")
    parser.add_argument("--synthetic", type=int, default=None,
                        help="Generate N synthetic predictions for testing (NOT for real analysis)")
    args = parser.parse_args()

    # ── Synthetic mode ──
    if args.synthetic is not None:
        generate_synthetic_predictions(args.synthetic, args.path)

    # ── Load predictions ──
    print(f"[LOAD] {args.path}")
    predictions = load_predictions(args.path)

    if not predictions:
        print("[ERROR] No predictions found. Run predict_only.py first, or use --synthetic N.")
        return 1

    total = len(predictions)
    verified = sum(1 for p in predictions if p.get("outcome") in ("CORRECT", "WRONG"))
    print(f"[DATA] {total} predictions loaded, {verified} verified")

    # ── Generate report ──
    report = generate_report(predictions, args.output_dir)

    # ── Print summary ──
    print()
    print("=" * 72)
    print("  SENECIO ORACLE_LAB — SUMMARY")
    print("=" * 72)
    print(f"  Total Predictions:    {total}")
    print(f"  Verified:             {verified}")

    cls = report.get("classification", {})
    if cls.get("accuracy") is not None:
        print(f"  Accuracy:             {cls['accuracy']:.2%}")
        print(f"  F1 (LONG):            {cls['f1_long']:.4f}")
        print(f"  F1 (SHORT):           {cls['f1_short']:.4f}")

    brier = report.get("probabilistic", {}).get("brier_score")
    if brier is not None:
        print(f"  Brier Score:          {brier:.6f}")

    ece = report.get("probabilistic", {}).get("ece", {}).get("ece")
    if ece is not None:
        print(f"  ECE:                  {ece:.6f}")

    sharpe_val = report.get("sharpe", {}).get("sharpe")
    if sharpe_val is not None:
        print(f"  Sharpe (annualized):  {sharpe_val:.4f}")

    print("=" * 72)

    return 0


if __name__ == "__main__":
    sys.exit(main())
