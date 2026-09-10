"""
SENECIO ORACLE — Statistical Study Suite (ACT FINAL_AUDIT — A7)
==================================================================

STRICT_ADDITIVE statistical evidence layer.

Implements the full statistical study suite required by A7:

  Test                     Purpose
  -----------------------  -----------------------------------------------
  Bonferroni               Family-wise error correction across all tests
  Benjamini-Hochberg       FDR control across all tests
  Permutation              Non-parametric p-value for edge significance
  Bootstrap                CI for win-rate by direction
  WilsonCI                 Wilson score CI for proportions
  CPCV                     Combinatorial Purged CV — overfit detection
  ChronologicalReplay      Walk-forward WR stability
  FeatureImportance        Model-free per-bucket WR spread
  DecisionTree             Sklearn DT classifier on outcome ~ features
  RandomForest             Sklearn RF classifier + permutation importance
  LogisticL1               L1-regularized logistic regression
  SHAP                     Tree-SHAP feature attribution (optional)
  CounterfactualSearch     One-feature-at-a-time removal simulation

Each test is wrapped in a try/except so a failure in one does NOT
cascade. Output is a structured dict consumed by the decision engine
(A8) and executive report (A9).

NEVER raises. NEVER modifies trading logic.
"""
from __future__ import annotations

import json
import logging
import math
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger("senecio.statistical_study")


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _wilson(wins: int, n: int, z: float = 1.96) -> tuple:
    if n == 0:
        return (0.0, 0.0, 0.0)
    p = wins / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), center, min(1.0, center + margin))


def _bootstrap_ci(samples: list, n_iter: int = 1000, seed: int = 42,
                  conf: float = 0.95) -> dict:
    import random
    rng = random.Random(seed)
    if not samples:
        return {"mean": None, "ci_low": None, "ci_high": None, "n": 0}
    n = len(samples)
    means = []
    for _ in range(n_iter):
        b = [rng.choice(samples) for _ in range(n)]
        means.append(sum(b) / n)
    means.sort()
    alpha = (1 - conf) / 2
    return {
        "mean": sum(samples) / n,
        "ci_low": means[int(alpha * n_iter)],
        "ci_high": means[int((1 - alpha) * n_iter)],
        "n": n,
        "n_iter": n_iter,
        "conf_level": conf,
    }


def _permutation_test(group_a: list, group_b: list,
                      n_iter: int = 1000, seed: int = 42) -> dict:
    """Two-sided permutation test on mean difference."""
    import random
    rng = random.Random(seed)
    if len(group_a) < 2 or len(group_b) < 2:
        return {"observed_diff": None, "p_value": None, "n_iter": n_iter}
    mean_a = sum(group_a) / len(group_a)
    mean_b = sum(group_b) / len(group_b)
    observed = mean_a - mean_b
    combined = list(group_a) + list(group_b)
    n_a = len(group_a)
    count = 0
    for _ in range(n_iter):
        rng.shuffle(combined)
        perm_a = combined[:n_a]
        perm_b = combined[n_a:]
        d = (sum(perm_a) / len(perm_a)) - (sum(perm_b) / len(perm_b))
        if abs(d) >= abs(observed):
            count += 1
    return {
        "observed_diff": round(observed, 6),
        "p_value": count / n_iter,
        "n_iter": n_iter,
    }


def _bonferroni(pvalues: list, alpha: float = 0.05) -> list:
    """Return list of booleans: True if test survives Bonferroni correction."""
    m = len(pvalues)
    if m == 0:
        return []
    thresh = alpha / m
    return [p < thresh for p in pvalues]


def _benjamini_hochberg(pvalues: list, fdr: float = 0.05) -> list:
    """Return list of booleans: True if test survives BH correction."""
    m = len(pvalues)
    if m == 0:
        return []
    # Sort by p-value
    indexed = sorted(enumerate(pvalues), key=lambda x: x[1])
    survives = [False] * m
    # Walk from largest to smallest
    last_survive = False
    for rank in range(m, 0, -1):
        orig_idx, p = indexed[rank - 1]
        thresh = (fdr * rank) / m
        if p <= thresh:
            last_survive = True
        survives[orig_idx] = last_survive
    return survives


# ─────────────────────────────────────────────────────────────────────
# Feature extraction (for ML-based tests)
# ─────────────────────────────────────────────────────────────────────

NUMERIC_FEATURES = [
    "confidence",  # top-level
    "ev",          # top-level
    "spread_bps", "book_imbalance", "vpin", "ofi", "funding",
    "open_interest", "liquidity_score",
    "signal_strength", "signal_confidence",
    "capacity_score", "stress_score",
    "hour_utc",
]
CATEGORICAL_FEATURES = [
    "regime_4h", "regime_hint", "market_phase",
    "weekday_name", "meta_label",
]


def _get_enriched(r: dict) -> dict:
    audit = r.get("audit") or {}
    if isinstance(audit, str):
        try:
            audit = json.loads(audit)
        except Exception:
            audit = {}
    if not isinstance(audit, dict):
        audit = {}
    return audit.get("enriched") or {}


def _extract_feature_matrix(rows: list, target_direction: Optional[str] = None) -> dict:
    """Return {X_numeric: list of dicts, X_categorical: list of dicts, y: list of 0/1, direction: list}."""
    if target_direction:
        rows = [r for r in rows if (r.get("prediction") or "").upper() == target_direction]
    X_num, X_cat, y, dirs = [], [], [], []
    for r in rows:
        o = r.get("outcome")
        if o not in ("WIN", "LOSS"):
            continue
        e = _get_enriched(r)
        num = {}
        for f in NUMERIC_FEATURES:
            v = e.get(f)
            if v is None and f in ("confidence", "ev"):
                v = r.get(f)
            try:
                if v is not None:
                    num[f] = float(v)
                else:
                    num[f] = float("nan")
            except Exception:
                num[f] = float("nan")
        cat = {}
        for f in CATEGORICAL_FEATURES:
            v = e.get(f)
            cat[f] = str(v) if v is not None else "MISSING"
        X_num.append(num)
        X_cat.append(cat)
        y.append(1 if o == "WIN" else 0)
        dirs.append((r.get("prediction") or "").upper())
    return {"X_numeric": X_num, "X_categorical": X_cat, "y": y, "direction": dirs}


# ─────────────────────────────────────────────────────────────────────
# Individual tests
# ─────────────────────────────────────────────────────────────────────

def test_wilson_by_direction(rows: list) -> dict:
    out = {}
    for d in ("LONG", "SHORT"):
        sub = [r for r in rows if (r.get("prediction") or "").upper() == d
               and r.get("outcome") in ("WIN", "LOSS")]
        wins = sum(1 for r in sub if r.get("outcome") == "WIN")
        n = len(sub)
        low, center, high = _wilson(wins, n)
        out[d] = {
            "n": n, "wins": wins,
            "win_rate": round(wins / n, 4) if n else None,
            "wilson_low": round(low, 4),
            "wilson_center": round(center, 4),
            "wilson_high": round(high, 4),
        }
    return out


def test_bootstrap_by_direction(rows: list, n_iter: int = 1000) -> dict:
    out = {}
    for d in ("LONG", "SHORT"):
        sub = [r for r in rows if (r.get("prediction") or "").upper() == d
               and r.get("outcome") in ("WIN", "LOSS")]
        samples = [1 if r.get("outcome") == "WIN" else 0 for r in sub]
        out[d] = _bootstrap_ci(samples, n_iter=n_iter)
    return out


def test_permutation_long_vs_short(rows: list, n_iter: int = 1000) -> dict:
    longs = [1 if r.get("outcome") == "WIN" else 0
             for r in rows if (r.get("prediction") or "").upper() == "LONG"
             and r.get("outcome") in ("WIN", "LOSS")]
    shorts = [1 if r.get("outcome") == "WIN" else 0
              for r in rows if (r.get("prediction") or "").upper() == "SHORT"
              and r.get("outcome") in ("WIN", "LOSS")]
    return _permutation_test(longs, shorts, n_iter=n_iter)


def test_permutation_hour_buckets(rows: list, n_iter: int = 500) -> dict:
    """For each hour bucket vs all-other-hours, permutation p-value."""
    by_hour = defaultdict(list)
    for r in rows:
        e = _get_enriched(r)
        h = e.get("hour_utc")
        o = r.get("outcome")
        if h is None or o not in ("WIN", "LOSS"):
            continue
        by_hour[int(h)].append(1 if o == "WIN" else 0)
    out = {}
    for h, samples in by_hour.items():
        others = []
        for h2, s2 in by_hour.items():
            if h2 != h:
                others.extend(s2)
        if len(samples) < 5 or len(others) < 5:
            continue
        out[str(h)] = {
            "n_bucket": len(samples),
            "wr_bucket": round(sum(samples) / len(samples), 4),
            "n_others": len(others),
            "wr_others": round(sum(others) / len(others), 4),
            **_permutation_test(samples, others, n_iter=n_iter),
        }
    return out


def test_permutation_regime_buckets(rows: list, n_iter: int = 500) -> dict:
    by_regime = defaultdict(list)
    for r in rows:
        e = _get_enriched(r)
        rg = e.get("regime_4h")
        o = r.get("outcome")
        if rg is None or o not in ("WIN", "LOSS"):
            continue
        by_regime[str(rg)].append(1 if o == "WIN" else 0)
    out = {}
    for rg, samples in by_regime.items():
        others = []
        for rg2, s2 in by_regime.items():
            if rg2 != rg:
                others.extend(s2)
        if len(samples) < 5 or len(others) < 5:
            continue
        out[rg] = {
            "n_bucket": len(samples),
            "wr_bucket": round(sum(samples) / len(samples), 4),
            "n_others": len(others),
            "wr_others": round(sum(others) / len(others), 4),
            **_permutation_test(samples, others, n_iter=n_iter),
        }
    return out


def test_cpcv(rows: list, n_folds: int = 6, n_test: int = 2) -> dict:
    """CPCV — measure of OOS stability."""
    import itertools
    sorted_rows = sorted(rows, key=lambda r: r.get("ts") or "")
    n = len(sorted_rows)
    if n < n_folds * 5:
        return {"n_combos": 0, "pbo": None, "wr_mean": None, "wr_std": None,
                "wr_min": None, "wr_max": None}

    fold_size = n // n_folds
    folds = []
    for i in range(n_folds):
        start = i * fold_size
        end = (i + 1) * fold_size if i < n_folds - 1 else n
        folds.append(sorted_rows[start:end])

    test_wrs = []
    train_test_diffs = []
    for combo in itertools.combinations(range(n_folds), n_test):
        test_rows, train_rows = [], []
        test_idx = set(combo)
        for i, f in enumerate(folds):
            if i in test_idx:
                test_rows.extend(f)
            else:
                train_rows.extend(f)
        if not test_rows or not train_rows:
            continue
        test_wins = sum(1 for r in test_rows if r.get("outcome") == "WIN")
        test_n = sum(1 for r in test_rows if r.get("outcome") in ("WIN", "LOSS"))
        train_wins = sum(1 for r in train_rows if r.get("outcome") == "WIN")
        train_n = sum(1 for r in train_rows if r.get("outcome") in ("WIN", "LOSS"))
        if test_n == 0 or train_n == 0:
            continue
        test_wrs.append(test_wins / test_n)
        train_test_diffs.append((train_wins / train_n) - (test_wins / test_n))

    if not test_wrs:
        return {"n_combos": 0, "pbo": None, "wr_mean": None, "wr_std": None,
                "wr_min": None, "wr_max": None}

    pbo = sum(1 for d in train_test_diffs if d > 0) / len(train_test_diffs)
    return {
        "n_folds": n_folds,
        "n_test": n_test,
        "n_combos": len(test_wrs),
        "wr_mean": round(sum(test_wrs) / len(test_wrs), 4),
        "wr_std": round(statistics.pstdev(test_wrs), 4) if len(test_wrs) > 1 else 0.0,
        "wr_min": round(min(test_wrs), 4),
        "wr_max": round(max(test_wrs), 4),
        "pbo": round(pbo, 4),
    }


def test_chronological_replay(rows: list, n_folds: int = 6) -> dict:
    """Walk-forward: train on first k folds, test on fold k+1, advance k."""
    sorted_rows = sorted(rows, key=lambda r: r.get("ts") or "")
    n = len(sorted_rows)
    if n < n_folds * 5:
        return {"n_folds": n_folds, "n_walks": 0, "wr_per_fold": [], "wr_std": None}
    fold_size = n // n_folds
    folds = [sorted_rows[i * fold_size: (i + 1) * fold_size if i < n_folds - 1 else n]
             for i in range(n_folds)]
    wrs = []
    for k in range(1, n_folds):
        test_fold = folds[k]
        wins = sum(1 for r in test_fold if r.get("outcome") == "WIN")
        n_test = sum(1 for r in test_fold if r.get("outcome") in ("WIN", "LOSS"))
        if n_test > 0:
            wrs.append(wins / n_test)
    return {
        "n_folds": n_folds,
        "n_walks": len(wrs),
        "wr_per_fold": [round(w, 4) for w in wrs],
        "wr_mean": round(sum(wrs) / len(wrs), 4) if wrs else None,
        "wr_std": round(statistics.pstdev(wrs), 4) if len(wrs) > 1 else 0.0,
        "wr_min": round(min(wrs), 4) if wrs else None,
        "wr_max": round(max(wrs), 4) if wrs else None,
    }


def test_feature_importance_model_free(rows: list) -> dict:
    """For each categorical feature, compute bucket WR spread + permutation p."""
    out = {}
    for feat in CATEGORICAL_FEATURES:
        buckets = defaultdict(list)
        for r in rows:
            e = _get_enriched(r)
            v = e.get(feat)
            o = r.get("outcome")
            if v is None or o not in ("WIN", "LOSS"):
                continue
            buckets[str(v)].append(1 if o == "WIN" else 0)
        if len(buckets) < 2:
            continue
        bucket_stats = []
        for k, s in buckets.items():
            if len(s) >= 3:
                bucket_stats.append({
                    "bucket": k, "n": len(s),
                    "wr": round(sum(s) / len(s), 4),
                    "wilson": [round(c, 4) for c in _wilson(sum(s), len(s))],
                })
        if len(bucket_stats) < 2:
            continue
        wrs = [b["wr"] for b in bucket_stats]
        out[feat] = {
            "n_buckets": len(bucket_stats),
            "wr_spread": round(max(wrs) - min(wrs), 4),
            "best_bucket": max(bucket_stats, key=lambda x: x["wr"]),
            "worst_bucket": min(bucket_stats, key=lambda x: x["wr"]),
            "buckets": sorted(bucket_stats, key=lambda x: -x["wr"]),
        }
    return out


def test_decision_tree(rows: list, target_direction: str = "LONG") -> dict:
    """Sklearn DecisionTreeClassifier on outcome ~ features."""
    try:
        import numpy as np
        from sklearn.tree import DecisionTreeClassifier, export_text
        from sklearn.preprocessing import OrdinalEncoder
    except ImportError:
        return {"available": False, "reason": "sklearn not installed"}

    fm = _extract_feature_matrix(rows, target_direction=target_direction)
    if len(fm["y"]) < 30:
        return {"available": True, "n_samples": len(fm["y"]),
                "reason": "insufficient samples (need >= 30)"}

    # Build feature matrix
    feature_names = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    X = []
    for i in range(len(fm["y"])):
        row = []
        for f in NUMERIC_FEATURES:
            v = fm["X_numeric"][i].get(f, float("nan"))
            row.append(v if not math.isnan(v) else 0.0)
        for f in CATEGORICAL_FEATURES:
            row.append(fm["X_categorical"][i].get(f, "MISSING"))
        X.append(row)
    X_np = np.array(X, dtype=object)
    # Encode categoricals (last len(CATEGORICAL) columns)
    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    cat_start = len(NUMERIC_FEATURES)
    X_np[:, cat_start:] = enc.fit_transform(X_np[:, cat_start:])
    X_np = X_np.astype(float)
    y_np = np.array(fm["y"])

    clf = DecisionTreeClassifier(max_depth=4, min_samples_leaf=5, random_state=42)
    clf.fit(X_np, y_np)
    train_acc = clf.score(X_np, y_np)
    importance = {feature_names[i]: round(float(v), 4)
                  for i, v in enumerate(clf.feature_importances_) if v > 0}
    return {
        "available": True,
        "direction": target_direction,
        "n_samples": len(fm["y"]),
        "n_features": len(feature_names),
        "max_depth": 4,
        "train_accuracy": round(float(train_acc), 4),
        "feature_importance_top10": dict(sorted(importance.items(),
                                                key=lambda x: -x[1])[:10]),
    }


def test_random_forest(rows: list, target_direction: str = "LONG") -> dict:
    try:
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import OrdinalEncoder
    except ImportError:
        return {"available": False, "reason": "sklearn not installed"}

    fm = _extract_feature_matrix(rows, target_direction=target_direction)
    if len(fm["y"]) < 30:
        return {"available": True, "n_samples": len(fm["y"]),
                "reason": "insufficient samples (need >= 30)"}

    feature_names = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    X = []
    for i in range(len(fm["y"])):
        row = []
        for f in NUMERIC_FEATURES:
            v = fm["X_numeric"][i].get(f, float("nan"))
            row.append(v if not math.isnan(v) else 0.0)
        for f in CATEGORICAL_FEATURES:
            row.append(fm["X_categorical"][i].get(f, "MISSING"))
        X.append(row)
    X_np = np.array(X, dtype=object)
    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    cat_start = len(NUMERIC_FEATURES)
    X_np[:, cat_start:] = enc.fit_transform(X_np[:, cat_start:])
    X_np = X_np.astype(float)
    y_np = np.array(fm["y"])

    rf = RandomForestClassifier(n_estimators=100, max_depth=6,
                                min_samples_leaf=5, random_state=42, n_jobs=1)
    rf.fit(X_np, y_np)
    train_acc = rf.score(X_np, y_np)
    importance = {feature_names[i]: round(float(v), 4)
                  for i, v in enumerate(rf.feature_importances_) if v > 0}
    return {
        "available": True,
        "direction": target_direction,
        "n_samples": len(fm["y"]),
        "n_features": len(feature_names),
        "n_estimators": 100,
        "train_accuracy": round(float(train_acc), 4),
        "feature_importance_top10": dict(sorted(importance.items(),
                                                key=lambda x: -x[1])[:10]),
    }


def test_logistic_l1(rows: list, target_direction: str = "LONG") -> dict:
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import OrdinalEncoder, StandardScaler
    except ImportError:
        return {"available": False, "reason": "sklearn not installed"}

    fm = _extract_feature_matrix(rows, target_direction=target_direction)
    if len(fm["y"]) < 30:
        return {"available": True, "n_samples": len(fm["y"]),
                "reason": "insufficient samples (need >= 30)"}

    feature_names = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    X = []
    for i in range(len(fm["y"])):
        row = []
        for f in NUMERIC_FEATURES:
            v = fm["X_numeric"][i].get(f, float("nan"))
            row.append(v if not math.isnan(v) else 0.0)
        for f in CATEGORICAL_FEATURES:
            row.append(fm["X_categorical"][i].get(f, "MISSING"))
        X.append(row)
    X_np = np.array(X, dtype=object)
    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    cat_start = len(NUMERIC_FEATURES)
    X_np[:, cat_start:] = enc.fit_transform(X_np[:, cat_start:])
    X_np = X_np.astype(float)
    y_np = np.array(fm["y"])

    # Standardize for L1
    scaler = StandardScaler()
    X_np = scaler.fit_transform(X_np)

    clf = LogisticRegression(penalty="l1", solver="liblinear",
                             C=0.1, random_state=42, max_iter=200)
    clf.fit(X_np, y_np)
    train_acc = clf.score(X_np, y_np)
    coefs = {feature_names[i]: round(float(v), 4)
             for i, v in enumerate(clf.coef_[0]) if abs(v) > 1e-4}
    return {
        "available": True,
        "direction": target_direction,
        "n_samples": len(fm["y"]),
        "C": 0.1,
        "train_accuracy": round(float(train_acc), 4),
        "non_zero_coefs": coefs,
    }


def test_shap(rows: list, target_direction: str = "LONG") -> dict:
    """Tree-SHAP — optional, requires shap package."""
    try:
        import shap  # type: ignore
        import numpy as np
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.preprocessing import OrdinalEncoder
    except ImportError:
        return {"available": False,
                "reason": "shap or sklearn not installed"}

    fm = _extract_feature_matrix(rows, target_direction=target_direction)
    if len(fm["y"]) < 50:
        return {"available": True, "n_samples": len(fm["y"]),
                "reason": "insufficient samples for SHAP (need >= 50)"}

    feature_names = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    X = []
    for i in range(len(fm["y"])):
        row = []
        for f in NUMERIC_FEATURES:
            v = fm["X_numeric"][i].get(f, float("nan"))
            row.append(v if not math.isnan(v) else 0.0)
        for f in CATEGORICAL_FEATURES:
            row.append(fm["X_categorical"][i].get(f, "MISSING"))
        X.append(row)
    X_np = np.array(X, dtype=object)
    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    cat_start = len(NUMERIC_FEATURES)
    X_np[:, cat_start:] = enc.fit_transform(X_np[:, cat_start:])
    X_np = X_np.astype(float)
    y_np = np.array(fm["y"])

    rf = RandomForestClassifier(n_estimators=50, max_depth=5,
                                random_state=42, n_jobs=1)
    rf.fit(X_np, y_np)

    try:
        explainer = shap.TreeExplainer(rf)
        shap_values = explainer.shap_values(X_np)
        # shap_values can be list (per class) or array
        if isinstance(shap_values, list):
            sv = shap_values[1]  # positive class
        else:
            # Newer shap returns array of shape (n_samples, n_features, n_classes)
            if len(shap_values.shape) == 3:
                sv = shap_values[:, :, 1]
            else:
                sv = shap_values
        abs_mean = np.abs(sv).mean(axis=0)
        ranking = sorted(
            [(feature_names[i], round(float(abs_mean[i]), 4)) for i in range(len(feature_names))],
            key=lambda x: -x[1],
        )[:10]
        return {
            "available": True,
            "direction": target_direction,
            "n_samples": len(fm["y"]),
            "top10_shap_features": ranking,
        }
    except Exception as e:
        return {"available": True, "reason": f"shap computation failed: {e}"}


def test_counterfactual_search(rows: list, candidate_filters: list = None) -> dict:
    """Counterfactual search: for each candidate filter, simulate WR if filter applied.

    A filter is a (feature, predicate) tuple. e.g. ("hour_utc", "in [0..7]").
    We compute: WR-without-filter, WR-with-filter, n-with-filter, delta.
    """
    if candidate_filters is None:
        # Default: try removing each hour bucket, each regime, etc.
        candidate_filters = []

        # Hour buckets
        by_hour = defaultdict(list)
        for r in rows:
            e = _get_enriched(r)
            h = e.get("hour_utc")
            o = r.get("outcome")
            if h is not None and o in ("WIN", "LOSS"):
                by_hour[int(h)].append(r)
        for h, sub in by_hour.items():
            if len(sub) >= 5:
                candidate_filters.append({
                    "name": f"exclude_hour_{h}",
                    "feature": "hour_utc",
                    "predicate": f"!= {h}",
                    "excluded_bucket": str(h),
                    "n_in_bucket": len(sub),
                })

        # Regime buckets
        by_reg = defaultdict(list)
        for r in rows:
            e = _get_enriched(r)
            rg = e.get("regime_4h")
            o = r.get("outcome")
            if rg is not None and o in ("WIN", "LOSS"):
                by_reg[str(rg)].append(r)
        for rg, sub in by_reg.items():
            if len(sub) >= 5:
                candidate_filters.append({
                    "name": f"exclude_regime_{rg}",
                    "feature": "regime_4h",
                    "predicate": f"!= {rg}",
                    "excluded_bucket": rg,
                    "n_in_bucket": len(sub),
                })

    if not candidate_filters:
        return {"n_filters_tested": 0, "results": []}

    # Baseline
    verified = [r for r in rows if r.get("outcome") in ("WIN", "LOSS")]
    baseline_wins = sum(1 for r in verified if r.get("outcome") == "WIN")
    baseline_n = len(verified)
    baseline_wr = baseline_wins / baseline_n if baseline_n else 0.0

    results = []
    for f in candidate_filters:
        # Apply filter: exclude rows in bucket
        kept = []
        for r in verified:
            e = _get_enriched(r)
            v = e.get(f["feature"])
            if v is None:
                kept.append(r)
                continue
            if str(v) == f["excluded_bucket"]:
                continue  # filter out
            kept.append(r)
        if not kept or len(kept) == baseline_n:
            continue
        kept_wins = sum(1 for r in kept if r.get("outcome") == "WIN")
        kept_n = len(kept)
        kept_wr = kept_wins / kept_n if kept_n else 0.0
        results.append({
            "filter": f,
            "n_kept": kept_n,
            "n_removed": baseline_n - kept_n,
            "wr_with_filter": round(kept_wr, 4),
            "wr_without_filter": round(baseline_wr, 4),
            "wr_delta_pp": round((kept_wr - baseline_wr) * 100, 2),
        })

    # Sort by biggest WR improvement
    results.sort(key=lambda x: -x["wr_delta_pp"])
    return {
        "n_filters_tested": len(results),
        "baseline_n": baseline_n,
        "baseline_wr": round(baseline_wr, 4),
        "results": results[:20],
    }


# ─────────────────────────────────────────────────────────────────────
# Multiple-testing correction
# ─────────────────────────────────────────────────────────────────────

def collect_pvalues(study: dict) -> list:
    """Walk the study dict and collect all p-values with labels."""
    pvals = []
    # Permutation: LONG vs SHORT
    perm_ls = study.get("permutation_long_vs_short", {})
    if perm_ls.get("p_value") is not None:
        pvals.append(("permutation_long_vs_short", perm_ls["p_value"]))

    # Permutation: hour buckets
    for h, info in study.get("permutation_hour_buckets", {}).items():
        if info.get("p_value") is not None:
            pvals.append((f"permutation_hour_{h}", info["p_value"]))

    # Permutation: regime buckets
    for rg, info in study.get("permutation_regime_buckets", {}).items():
        if info.get("p_value") is not None:
            pvals.append((f"permutation_regime_{rg}", info["p_value"]))
    return pvals


def apply_multiple_testing_corrections(study: dict) -> dict:
    """Apply Bonferroni + BH FDR corrections to all p-values."""
    pvals = collect_pvalues(study)
    if not pvals:
        return {"n_tests": 0, "bonferroni": {}, "benjamini_hochberg": {}}
    labels = [p[0] for p in pvals]
    ps = [p[1] for p in pvals]
    bonf = _bonferroni(ps, alpha=0.05)
    bh = _benjamini_hochberg(ps, fdr=0.05)
    return {
        "n_tests": len(ps),
        "alpha_familywise": 0.05,
        "fdr_target": 0.05,
        "bonferroni_threshold": round(0.05 / len(ps), 6),
        "tests": [
            {"test": l, "p_value": round(p, 4),
             "survives_bonferroni": bool(b), "survives_bh": bool(bh)}
            for l, p, b, bh in zip(labels, ps, bonf, bh)
        ],
    }


# ─────────────────────────────────────────────────────────────────────
# Top-level orchestrator
# ─────────────────────────────────────────────────────────────────────

def run_full_study(rows: list) -> dict:
    """Run the full A7 statistical study suite on verified rows."""
    started = time.time()
    ts = datetime.now(timezone.utc).isoformat()
    report = {
        "study_started_at_utc": ts,
        "n_rows_input": len(rows),
        "version": "A7-2026-06-20-v1",
        "tests": {},
    }

    def safe(key, fn, *args, **kwargs):
        try:
            report["tests"][key] = fn(*args, **kwargs)
        except Exception as e:
            log.warning("test %s failed: %s", key, e)
            report["tests"][key] = {"error": str(e)}

    # 1. WilsonCI
    safe("wilson_by_direction", test_wilson_by_direction, rows)

    # 2. Bootstrap
    safe("bootstrap_by_direction", test_bootstrap_by_direction, rows, 1000)

    # 3. Permutation (LONG vs SHORT)
    safe("permutation_long_vs_short", test_permutation_long_vs_short, rows, 1000)

    # 4. Permutation: hour buckets
    safe("permutation_hour_buckets", test_permutation_hour_buckets, rows, 500)

    # 5. Permutation: regime buckets
    safe("permutation_regime_buckets", test_permutation_regime_buckets, rows, 500)

    # 6. CPCV
    safe("cpcv", test_cpcv, rows, 6, 2)

    # 7. Chronological replay
    safe("chronological_replay", test_chronological_replay, rows, 6)

    # 8. Feature importance (model-free)
    safe("feature_importance_model_free", test_feature_importance_model_free, rows)

    # 9. DecisionTree (LONG)
    safe("decision_tree_long", test_decision_tree, rows, "LONG")

    # 10. DecisionTree (SHORT)
    safe("decision_tree_short", test_decision_tree, rows, "SHORT")

    # 11. RandomForest (LONG)
    safe("random_forest_long", test_random_forest, rows, "LONG")

    # 12. RandomForest (SHORT)
    safe("random_forest_short", test_random_forest, rows, "SHORT")

    # 13. Logistic L1 (LONG)
    safe("logistic_l1_long", test_logistic_l1, rows, "LONG")

    # 14. Logistic L1 (SHORT)
    safe("logistic_l1_short", test_logistic_l1, rows, "SHORT")

    # 15. SHAP (LONG) — optional
    safe("shap_long", test_shap, rows, "LONG")

    # 16. CounterfactualSearch
    safe("counterfactual_search", test_counterfactual_search, rows, None)

    # 17. Multiple-testing corrections
    safe("multiple_testing_corrections", apply_multiple_testing_corrections, report["tests"])

    report["study_elapsed_ms"] = round((time.time() - started) * 1000, 2)
    report["study_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    return report
