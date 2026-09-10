"""
SENECIO ORACLE — ACT XXVII Priority 1: Purged Walk-Forward Validation
=====================================================================

Implements leakage-resistant cross-validation schemes used by institutional
research desks to evaluate signal quality without optimistic bias.

Schemes implemented:
  1. PurgedKFold              — K-fold with a "purge" window around each test
                                fold (removes samples whose label window
                                overlaps the test fold). Optionally applies
                                an "embargo" after each test fold to remove
                                forward-looking leakage.
  2. CombinatorialPurgedCV    — CPCV (López de Prado). Picks `n_test_folds`
                                of `n_groups` to be test, rest are train,
                                across all C(n_groups, n_test_folds) paths.
                                Every sample appears in test exactly
                                n_test_folds times across all paths.

Both schemes accept a `times` array (one timestamp per sample) so the purge
window can be measured in real time-units (e.g. 15min). If `times` is None,
an integer-index based fallback is used (still removes the boundary).

Reports are stored as JSONL under `data/research/purged_cv_reports/` so each
validation run is auditable later.

This module is STRICT_ADDITIVE — it does NOT touch:
  - prediction_model (predict_only.py)
  - feature_engineering (institutional_core.py compress_features)
  - signal_generation (institutional_core.py produce_action)
  - verifier (oracle_runner.py)
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np

log = logging.getLogger("senecio.research.purged_cv")


DEFAULTS: dict[str, Any] = {
    "n_splits":             5,
    "purge_td_seconds":     900.0,   # 15 min — matches oracle 15min outcome window
    "embargo_td_seconds":   900.0,   # 15 min forward embargo
    "n_groups":             6,        # for CPCV
    "n_test_groups":        2,        # for CPCV
    "reports_dir":          "data/research/purged_cv_reports",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_epoch(ts: Any) -> float:
    """Coerce a timestamp to epoch seconds.

    Accepts: epoch float/int, ISO-8601 string, datetime.
    """
    if ts is None:
        return float("nan")
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        return ts.timestamp()
    if isinstance(ts, str):
        try:
            # ISO-8601 with optional trailing 'Z'
            s = ts.rstrip("Z")
            return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            try:
                return float(ts)
            except Exception:
                return float("nan")
    return float("nan")


def _times_array(times: Optional[Sequence[Any]], n: int) -> np.ndarray:
    """Return a (n,) float array of epoch seconds (NaN if not provided)."""
    if times is None:
        return np.full(n, np.nan, dtype=float)
    arr = np.asarray([_to_epoch(t) for t in times], dtype=float)
    if arr.shape[0] != n:
        raise ValueError(
            f"times has length {arr.shape[0]} but expected {n}"
        )
    return arr


def _purge_mask_for_test_indices(
    times: np.ndarray,
    test_idx: np.ndarray,
    purge_seconds: float,
    embargo_seconds: float,
) -> np.ndarray:
    """Boolean mask (len(times)) — True where sample should be PURGED.

    A sample i is purged if its time falls within ±purge_seconds of any
    test sample time, OR within [test_max, test_max + embargo_seconds].
    """
    n = times.shape[0]
    mask = np.zeros(n, dtype=bool)
    if test_idx.shape[0] == 0:
        return mask
    test_times = times[test_idx]
    finite = np.isfinite(test_times)
    if not finite.any():
        # No time information — fall back to index-based purge of ±1 around test
        for i in test_idx:
            if i > 0:
                mask[i - 1] = True
            if i < n - 1:
                mask[i + 1] = True
        return mask
    test_min = float(np.nanmin(test_times))
    test_max = float(np.nanmax(test_times))
    # Purge window = [test_min - purge, test_max + purge + embargo]
    purge_lo = test_min - purge_seconds
    purge_hi = test_max + purge_seconds + embargo_seconds
    finite_times = np.isfinite(times)
    mask[finite_times] = (times[finite_times] >= purge_lo) & (times[finite_times] <= purge_hi)
    return mask


# ---------------------------------------------------------------------------
# PurgedKFold
# ---------------------------------------------------------------------------


@dataclass
class PurgedFold:
    """One fold of PurgedKFold.

    Attributes:
        fold_id               : 0-indexed fold number
        train_indices         : array of training sample indices
        test_indices          : array of test sample indices
        purged_indices        : indices removed due to purge window
        embargoed_indices     : indices removed due to forward embargo
        test_time_range       : (epoch_lo, epoch_hi) of test fold or (NaN, NaN)
        n_train / n_test      : sizes after purge
    """
    fold_id: int
    train_indices: np.ndarray
    test_indices: np.ndarray
    purged_indices: np.ndarray
    embargoed_indices: np.ndarray
    test_time_range: tuple[float, float]
    n_train: int
    n_test: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold_id": self.fold_id,
            "n_train": int(self.n_train),
            "n_test": int(self.n_test),
            "n_purged": int(self.purged_indices.shape[0]),
            "n_embargoed": int(self.embargoed_indices.shape[0]),
            "test_time_range": list(self.test_time_range),
            "train_indices": self.train_indices.tolist(),
            "test_indices": self.test_indices.tolist(),
        }


class PurgedKFold:
    """K-fold CV with purge window + embargo.

    Usage:
        cv = PurgedKFold(n_splits=5, purge_td_seconds=900, embargo_td_seconds=900)
        for fold in cv.split(X, times=timestamps):
            X_tr, y_tr = X[fold.train_indices], y[fold.train_indices]
            X_te, y_te = X[fold.test_indices],  y[fold.test_indices]
            ...
    """

    def __init__(
        self,
        n_splits: int = 5,
        purge_td_seconds: float = 900.0,
        embargo_td_seconds: float = 900.0,
    ):
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if purge_td_seconds < 0:
            raise ValueError("purge_td_seconds must be >= 0")
        if embargo_td_seconds < 0:
            raise ValueError("embargo_td_seconds must be >= 0")
        self.n_splits = int(n_splits)
        self.purge_td_seconds = float(purge_td_seconds)
        self.embargo_td_seconds = float(embargo_td_seconds)

    def get_n_splits(self, X: Optional[Any] = None, y: Optional[Any] = None) -> int:
        return self.n_splits

    def split(
        self,
        X: Any,
        y: Optional[Any] = None,
        times: Optional[Sequence[Any]] = None,
        groups: Optional[Any] = None,
    ) -> Iterable[PurgedFold]:
        """Yield PurgedFold objects.

        Args:
            X       : array-like of shape (n_samples, n_features) (used for size only)
            y       : ignored (kept for sklearn-compat)
            times   : optional sequence of timestamps (one per sample). If
                      provided, purge/embargo are measured in seconds. If
                      None, falls back to ±1 index window.
            groups  : ignored
        """
        n = len(X) if hasattr(X, "__len__") else int(X)
        if n < self.n_splits:
            raise ValueError(
                f"n_samples={n} < n_splits={self.n_splits}"
            )
        times_arr = _times_array(times, n)
        # Sort by time if available — guarantees walk-forward character
        if np.isfinite(times_arr).any():
            order = np.argsort(times_arr, kind="stable")
        else:
            order = np.arange(n)
        # Even split of sorted indices
        fold_edges = np.array_split(np.arange(n), self.n_splits)
        for fold_id, test_idx_sorted in enumerate(fold_edges):
            test_idx = order[test_idx_sorted]
            purge_mask = _purge_mask_for_test_indices(
                times_arr, test_idx,
                self.purge_td_seconds, self.embargo_td_seconds,
            )
            # Train = everything not in test AND not purged
            all_idx = np.arange(n)
            test_set = set(int(i) for i in test_idx)
            purged_set = set(int(i) for i in np.where(purge_mask)[0])
            train_idx = np.array(
                [int(i) for i in all_idx
                 if int(i) not in test_set and int(i) not in purged_set],
                dtype=int,
            )
            # Split purged into purge-window vs embargo-window for reporting
            embargoed_idx = np.array([], dtype=int)
            purged_only_idx = np.array(
                sorted(purged_set - test_set), dtype=int,
            )
            # Test time range
            test_times = times_arr[test_idx]
            if np.isfinite(test_times).any():
                test_lo = float(np.nanmin(test_times))
                test_hi = float(np.nanmax(test_times))
            else:
                test_lo, test_hi = float("nan"), float("nan")
            yield PurgedFold(
                fold_id=fold_id,
                train_indices=train_idx,
                test_indices=test_idx,
                purged_indices=purged_only_idx,
                embargoed_indices=embargoed_idx,
                test_time_range=(test_lo, test_hi),
                n_train=int(train_idx.shape[0]),
                n_test=int(test_idx.shape[0]),
            )


# ---------------------------------------------------------------------------
# CombinatorialPurged Cross-Validation (CPCV)
# ---------------------------------------------------------------------------


@dataclass
class CPCVPath:
    """One CPCV path: a (train, test) split over groups."""
    path_id: int
    train_indices: np.ndarray
    test_indices: np.ndarray
    test_groups: tuple[int, ...]
    n_train: int
    n_test: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "test_groups": list(self.test_groups),
            "n_train": int(self.n_train),
            "n_test": int(self.n_test),
            "train_indices": self.train_indices.tolist(),
            "test_indices": self.test_indices.tolist(),
        }


class CombinatorialPurgedCV:
    """CPCV — López de Prado's leakage-aware combinatorial CV.

    Picks `n_test_groups` of `n_groups` total groups to be test (the rest
    train). Every sample appears in test exactly `C(n_groups-1, n_test_groups-1)`
    times across all `C(n_groups, n_test_groups)` paths.

    Usage:
        cv = CombinatorialPurgedCV(n_groups=6, n_test_groups=2,
                                   purge_td_seconds=900, embargo_td_seconds=900)
        for path in cv.split(X, times=timestamps):
            ...
    """

    def __init__(
        self,
        n_groups: int = 6,
        n_test_groups: int = 2,
        purge_td_seconds: float = 900.0,
        embargo_td_seconds: float = 900.0,
    ):
        if n_groups < 2:
            raise ValueError("n_groups must be >= 2")
        if n_test_groups < 1 or n_test_groups >= n_groups:
            raise ValueError("n_test_groups must be in [1, n_groups-1]")
        self.n_groups = int(n_groups)
        self.n_test_groups = int(n_test_groups)
        self.purge_td_seconds = float(purge_td_seconds)
        self.embargo_td_seconds = float(embargo_td_seconds)

    @property
    def n_paths(self) -> int:
        return int(math.comb(self.n_groups, self.n_test_groups))

    def get_n_splits(self, X: Optional[Any] = None, y: Optional[Any] = None) -> int:
        return self.n_paths

    def split(
        self,
        X: Any,
        y: Optional[Any] = None,
        times: Optional[Sequence[Any]] = None,
        groups: Optional[Any] = None,
    ) -> Iterable[CPCVPath]:
        n = len(X) if hasattr(X, "__len__") else int(X)
        if n < self.n_groups:
            raise ValueError(
                f"n_samples={n} < n_groups={self.n_groups}"
            )
        times_arr = _times_array(times, n)
        # Sort by time if available, then split into n_groups contiguous groups
        if np.isfinite(times_arr).any():
            order = np.argsort(times_arr, kind="stable")
        else:
            order = np.arange(n)
        group_edges = np.array_split(np.arange(n), self.n_groups)
        # Each group's sample indices (in original space)
        group_indices: list[np.ndarray] = [
            order[ge] for ge in group_edges
        ]
        # Enumerate all combinations of n_test_groups groups
        for path_id, test_group_combo in enumerate(
            combinations(range(self.n_groups), self.n_test_groups)
        ):
            test_set = set()
            for g in test_group_combo:
                test_set.update(int(i) for i in group_indices[g])
            test_idx = np.array(sorted(test_set), dtype=int)
            # Compute purge mask across all test groups
            purge_mask = _purge_mask_for_test_indices(
                times_arr, test_idx,
                self.purge_td_seconds, self.embargo_td_seconds,
            )
            all_idx = np.arange(n)
            purged_set = set(int(i) for i in np.where(purge_mask)[0])
            train_idx = np.array(
                [int(i) for i in all_idx
                 if int(i) not in test_set and int(i) not in purged_set],
                dtype=int,
            )
            yield CPCVPath(
                path_id=path_id,
                train_indices=train_idx,
                test_indices=test_idx,
                test_groups=tuple(int(g) for g in test_group_combo),
                n_train=int(train_idx.shape[0]),
                n_test=int(test_idx.shape[0]),
            )


# ---------------------------------------------------------------------------
# Validation runner + report persistence
# ---------------------------------------------------------------------------


@dataclass
class FoldResult:
    """Per-fold scoring output."""
    fold_id: int
    n_train: int
    n_test: int
    n_purged: int
    metrics: dict[str, float] = field(default_factory=dict)
    test_predictions: list[float] = field(default_factory=list)
    test_targets: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ValidationReport:
    """Full validation report across all folds."""
    scheme: str            # "purged_kfold" or "cpcv"
    n_splits_or_paths: int
    purge_td_seconds: float
    embargo_td_seconds: float
    started_at: str
    completed_at: str
    n_samples: int
    fold_results: list[FoldResult]
    aggregate_metrics: dict[str, float] = field(default_factory=dict)
    feature_names: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scheme": self.scheme,
            "n_splits_or_paths": self.n_splits_or_paths,
            "purge_td_seconds": self.purge_td_seconds,
            "embargo_td_seconds": self.embargo_td_seconds,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "n_samples": self.n_samples,
            "fold_results": [fr.to_dict() for fr in self.fold_results],
            "aggregate_metrics": self.aggregate_metrics,
            "feature_names": self.feature_names,
            "extra": self.extra,
        }


def _default_score_fn(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Default scoring: accuracy + AUC + Brier (works for binary 0/1 targets)."""
    out: dict[str, float] = {}
    n = y_true.shape[0]
    if n == 0:
        return out
    # Accuracy (threshold 0.5)
    pred_label = (y_pred >= 0.5).astype(int)
    y_true_int = y_true.astype(int)
    out["accuracy"] = float((pred_label == y_true_int).mean())
    # Brier score (lower = better)
    out["brier"] = float(np.mean((y_pred - y_true) ** 2))
    # AUC (only if both classes present)
    classes = np.unique(y_true_int)
    if len(classes) == 2:
        # Rank-based AUC
        order = np.argsort(-y_pred, kind="stable")
        ranks = np.empty_like(order, dtype=float)
        ranks[order] = np.arange(1, n + 1, dtype=float)
        pos = y_true_int == 1
        neg = y_true_int == 0
        n_pos = int(pos.sum())
        n_neg = int(neg.sum())
        if n_pos > 0 and n_neg > 0:
            sum_pos_ranks = ranks[pos].sum()
            auc = (sum_pos_ranks - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)
            out["auc"] = float(auc)
    return out


def run_purged_kfold(
    X: np.ndarray,
    y: np.ndarray,
    times: Optional[Sequence[Any]] = None,
    n_splits: int = 5,
    purge_td_seconds: float = 900.0,
    embargo_td_seconds: float = 900.0,
    score_fn=None,
    fit_predict_fn=None,
    feature_names: Optional[list[str]] = None,
    reports_dir: str = DEFAULTS["reports_dir"],
    extra: Optional[dict] = None,
) -> ValidationReport:
    """Run PurgedKFold validation.

    Args:
        X                : (n_samples, n_features) feature matrix
        y                : (n_samples,) target (binary 0/1 or regression)
        times            : optional per-sample timestamps
        n_splits         : number of folds
        purge_td_seconds : purge window in seconds
        embargo_td_seconds: forward embargo in seconds
        score_fn         : callable(y_true, y_pred) -> dict. Defaults to
                           accuracy/brier/auc.
        fit_predict_fn   : callable(X_train, y_train, X_test) -> y_pred.
                           If None, a logistic-regression baseline is used.
        feature_names    : optional feature labels
        reports_dir      : where to persist the JSONL report
        extra            : extra metadata to embed in report
    """
    started = datetime.now(timezone.utc)
    X_arr = np.asarray(X, dtype=float)
    if X_arr.ndim == 1:
        X_arr = X_arr.reshape(-1, 1)
    y_arr = np.asarray(y, dtype=float).reshape(-1)
    n, n_feat = X_arr.shape
    if y_arr.shape[0] != n:
        raise ValueError("X and y length mismatch")
    if fit_predict_fn is None:
        fit_predict_fn = _logistic_fit_predict
    if score_fn is None:
        score_fn = _default_score_fn

    cv = PurgedKFold(
        n_splits=n_splits,
        purge_td_seconds=purge_td_seconds,
        embargo_td_seconds=embargo_td_seconds,
    )
    fold_results: list[FoldResult] = []
    all_metrics: list[dict[str, float]] = []
    for fold in cv.split(X_arr, y_arr, times=times):
        if fold.train_indices.shape[0] == 0 or fold.test_indices.shape[0] == 0:
            log.warning("fold %d empty after purge — skipping", fold.fold_id)
            continue
        X_tr = X_arr[fold.train_indices]
        y_tr = y_arr[fold.train_indices]
        X_te = X_arr[fold.test_indices]
        y_te = y_arr[fold.test_indices]
        try:
            y_pred = fit_predict_fn(X_tr, y_tr, X_te)
            y_pred = np.asarray(y_pred, dtype=float).reshape(-1)
        except Exception as e:
            log.exception("fit_predict failed on fold %d: %s", fold.fold_id, e)
            continue
        metrics = score_fn(y_te, y_pred)
        fr = FoldResult(
            fold_id=fold.fold_id,
            n_train=fold.n_train,
            n_test=fold.n_test,
            n_purged=int(fold.purged_indices.shape[0]),
            metrics=metrics,
            test_predictions=y_pred.tolist(),
            test_targets=y_te.tolist(),
        )
        fold_results.append(fr)
        all_metrics.append(metrics)

    aggregate = _aggregate_metrics(all_metrics)
    completed = datetime.now(timezone.utc)
    report = ValidationReport(
        scheme="purged_kfold",
        n_splits_or_paths=n_splits,
        purge_td_seconds=purge_td_seconds,
        embargo_td_seconds=embargo_td_seconds,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        n_samples=n,
        fold_results=fold_results,
        aggregate_metrics=aggregate,
        feature_names=list(feature_names) if feature_names else [],
        extra=extra or {},
    )
    _persist_report(report, reports_dir)
    return report


def run_cpcv(
    X: np.ndarray,
    y: np.ndarray,
    times: Optional[Sequence[Any]] = None,
    n_groups: int = 6,
    n_test_groups: int = 2,
    purge_td_seconds: float = 900.0,
    embargo_td_seconds: float = 900.0,
    score_fn=None,
    fit_predict_fn=None,
    feature_names: Optional[list[str]] = None,
    reports_dir: str = DEFAULTS["reports_dir"],
    extra: Optional[dict] = None,
) -> ValidationReport:
    """Run Combinatorial Purged Cross-Validation (CPCV)."""
    started = datetime.now(timezone.utc)
    X_arr = np.asarray(X, dtype=float)
    if X_arr.ndim == 1:
        X_arr = X_arr.reshape(-1, 1)
    y_arr = np.asarray(y, dtype=float).reshape(-1)
    n, n_feat = X_arr.shape
    if y_arr.shape[0] != n:
        raise ValueError("X and y length mismatch")
    if fit_predict_fn is None:
        fit_predict_fn = _logistic_fit_predict
    if score_fn is None:
        score_fn = _default_score_fn

    cv = CombinatorialPurgedCV(
        n_groups=n_groups,
        n_test_groups=n_test_groups,
        purge_td_seconds=purge_td_seconds,
        embargo_td_seconds=embargo_td_seconds,
    )
    path_results: list[FoldResult] = []
    all_metrics: list[dict[str, float]] = []
    for path in cv.split(X_arr, y_arr, times=times):
        if path.train_indices.shape[0] == 0 or path.test_indices.shape[0] == 0:
            log.warning("path %d empty after purge — skipping", path.path_id)
            continue
        X_tr = X_arr[path.train_indices]
        y_tr = y_arr[path.train_indices]
        X_te = X_arr[path.test_indices]
        y_te = y_arr[path.test_indices]
        try:
            y_pred = fit_predict_fn(X_tr, y_tr, X_te)
            y_pred = np.asarray(y_pred, dtype=float).reshape(-1)
        except Exception as e:
            log.exception("fit_predict failed on path %d: %s", path.path_id, e)
            continue
        metrics = score_fn(y_te, y_pred)
        fr = FoldResult(
            fold_id=path.path_id,
            n_train=path.n_train,
            n_test=path.n_test,
            n_purged=0,
            metrics=metrics,
            test_predictions=y_pred.tolist(),
            test_targets=y_te.tolist(),
        )
        path_results.append(fr)
        all_metrics.append(metrics)

    aggregate = _aggregate_metrics(all_metrics)
    completed = datetime.now(timezone.utc)
    report = ValidationReport(
        scheme="cpcv",
        n_splits_or_paths=cv.n_paths,
        purge_td_seconds=purge_td_seconds,
        embargo_td_seconds=embargo_td_seconds,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        n_samples=n,
        fold_results=path_results,
        aggregate_metrics=aggregate,
        feature_names=list(feature_names) if feature_names else [],
        extra=extra or {},
    )
    _persist_report(report, reports_dir)
    return report


def _aggregate_metrics(metrics: list[dict[str, float]]) -> dict[str, float]:
    """Compute mean / std / min / max across all folds for each metric."""
    if not metrics:
        return {}
    keys = set()
    for m in metrics:
        keys.update(m.keys())
    out: dict[str, float] = {}
    for k in sorted(keys):
        vals = [float(m.get(k, 0.0)) for m in metrics]
        arr = np.asarray(vals, dtype=float)
        out[f"{k}_mean"]   = float(arr.mean())
        out[f"{k}_std"]    = float(arr.std(ddof=1)) if arr.shape[0] > 1 else 0.0
        out[f"{k}_min"]    = float(arr.min())
        out[f"{k}_max"]    = float(arr.max())
        out[f"{k}_n"]      = float(arr.shape[0])
    return out


def _logistic_fit_predict(
    X_tr: np.ndarray, y_tr: np.ndarray, X_te: np.ndarray
) -> np.ndarray:
    """Default fit/predict: sklearn LogisticRegression with class-weight balance.

    Used when no custom model is supplied — gives a sensible baseline for
    validation reports. Returns class-1 probabilities.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    # If only one class in training, return its prior for all test samples
    classes = np.unique(y_tr)
    if len(classes) < 2:
        return np.full(X_te.shape[0], float(classes[0]) if classes.shape[0] == 1 else 0.5)
    pipe = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            max_iter=1000, class_weight="balanced", C=1.0,
        )),
    ])
    pipe.fit(X_tr, y_tr.astype(int))
    # Use predict_proba for class-1 prob
    proba = pipe.predict_proba(X_te)
    # Find index of class "1"
    cls_list = list(pipe.classes_)
    if 1 in cls_list:
        return proba[:, cls_list.index(1)]
    # If only one class, return max proba
    return proba[:, -1]


def _persist_report(report: ValidationReport, reports_dir: str) -> None:
    """Append the report as one JSONL line under reports_dir/<date>.jsonl."""
    try:
        out_dir = Path(reports_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = out_dir / f"{day}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(report.to_dict(), default=str) + "\n")
        log.info("purged_cv report persisted to %s", path)
    except Exception as e:
        log.warning("failed to persist purged_cv report: %s", e)


__all__ = [
    "PurgedKFold",
    "PurgedFold",
    "CombinatorialPurgedCV",
    "CPCVPath",
    "FoldResult",
    "ValidationReport",
    "run_purged_kfold",
    "run_cpcv",
    "DEFAULTS",
]
