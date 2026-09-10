"""
SENECIO ORACLE — ACT XXVII Priority 3: Drift Detection
=======================================================

Implements four concept-drift detectors and a unified `DriftMonitor` that
runs them on the live oracle output stream and emits automatic warnings.

Detectors:
  - PSI (Population Stability Index)   — compares two distributions
  - KS Drift (Kolmogorov-Smirnov)      — tests two samples come from same dist
  - Page-Hinkley                       — sequential change detector
  - ADWIN (Adaptive Windowing)         — sliding window with adaptive cut

Each detector is independent and stateful; the `DriftMonitor` fans a single
observation out to all of them and aggregates the resulting warnings.

Warning persistence:
  Warnings are appended as JSONL under `data/research/drift_warnings/` so
  the operator can audit every drift signal later.

This module is STRICT_ADDITIVE — does NOT touch prediction/feature/signal/
verifier layers. It only consumes prediction records (which already exist
in predictions.jsonl + Supabase).
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from scipy import stats as sp_stats

log = logging.getLogger("senecio.research.drift")


DEFAULTS: dict[str, Any] = {
    "warnings_dir":             "data/research/drift_warnings",
    # PSI
    "psi_n_bins":               10,
    "psi_warn_threshold":       0.10,    # 0.1 = "small drift"
    "psi_alert_threshold":      0.25,    # 0.25 = "significant drift"
    # KS
    "ks_alpha":                 0.05,
    # Page-Hinkley
    "ph_threshold":             50.0,
    "ph_drift_threshold":       10.0,
    "ph_min_observations":      30,
    # ADWIN
    "adwin_delta":              0.002,
    "adwin_min_window":         30,
    "adwin_max_window":         5000,
}


# ---------------------------------------------------------------------------
# PSI — Population Stability Index
# ---------------------------------------------------------------------------


def psi_score(
    reference: np.ndarray,
    current: np.ndarray,
    n_bins: int = 10,
    bin_strategy: str = "quantile",
) -> float:
    """Compute Population Stability Index between two samples.

    PSI = Σ_bin (cur_pct - ref_pct) * ln(cur_pct / ref_pct)

    Interpretation:
      < 0.10  — no significant change
      0.10-0.25 — small drift
      > 0.25  — significant drift

    Args:
        reference : baseline distribution (e.g. first 30 days of predictions)
        current   : current distribution (e.g. last 7 days)
        n_bins    : number of bins to discretize the distributions
        bin_strategy : "quantile" (equal-frequency on reference) or "uniform"
                       (equal-width over [min, max] of reference)
    """
    ref = np.asarray(reference, dtype=float).reshape(-1)
    cur = np.asarray(current, dtype=float).reshape(-1)
    if ref.shape[0] == 0 or cur.shape[0] == 0:
        return 0.0
    # Define bin edges from reference
    if bin_strategy == "quantile":
        edges = np.quantile(ref, np.linspace(0.0, 1.0, n_bins + 1))
    else:
        lo, hi = float(ref.min()), float(ref.max())
        edges = np.linspace(lo, hi, n_bins + 1)
    # Ensure strictly increasing edges
    edges = np.unique(edges)
    if edges.shape[0] < 2:
        return 0.0
    # Apply same edges to both
    ref_counts, _ = np.histogram(ref, bins=edges)
    cur_counts, _ = np.histogram(cur, bins=edges)
    # Convert to proportions with smoothing
    eps = 1e-6
    ref_pct = ref_counts / max(ref_counts.sum(), 1)
    cur_pct = cur_counts / max(cur_counts.sum(), 1)
    ref_pct = np.clip(ref_pct, eps, None)
    cur_pct = np.clip(cur_pct, eps, None)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


class PSIDetector:
    """Sequential PSI detector: compares a fixed reference window to a
    growing current window. Emits warning when PSI > threshold.
    """

    def __init__(
        self,
        reference: Optional[np.ndarray] = None,
        n_bins: int = DEFAULTS["psi_n_bins"],
        warn_threshold: float = DEFAULTS["psi_warn_threshold"],
        alert_threshold: float = DEFAULTS["psi_alert_threshold"],
    ):
        self.reference: Optional[np.ndarray] = (
            np.asarray(reference, dtype=float).reshape(-1)
            if reference is not None else None
        )
        self.n_bins = int(n_bins)
        self.warn_threshold = float(warn_threshold)
        self.alert_threshold = float(alert_threshold)
        self.current: list[float] = []
        self.last_psi: float = 0.0

    def set_reference(self, ref: np.ndarray) -> None:
        self.reference = np.asarray(ref, dtype=float).reshape(-1)

    def update(self, value: float) -> Optional["DriftWarning"]:
        if self.reference is None:
            return None
        self.current.append(float(value))
        # Need at least 30 samples in current to compute meaningful PSI
        if len(self.current) < 30:
            return None
        cur_arr = np.asarray(self.current, dtype=float)
        self.last_psi = psi_score(self.reference, cur_arr, n_bins=self.n_bins)
        if self.last_psi >= self.alert_threshold:
            return DriftWarning(
                detector="psi",
                severity="alert",
                score=self.last_psi,
                threshold=self.alert_threshold,
                message=f"PSI={self.last_psi:.4f} >= alert threshold {self.alert_threshold}",
                ts=datetime.now(timezone.utc).isoformat(),
                extra={"n_reference": int(self.reference.shape[0]),
                       "n_current": int(cur_arr.shape[0])},
            )
        if self.last_psi >= self.warn_threshold:
            return DriftWarning(
                detector="psi",
                severity="warning",
                score=self.last_psi,
                threshold=self.warn_threshold,
                message=f"PSI={self.last_psi:.4f} >= warn threshold {self.warn_threshold}",
                ts=datetime.now(timezone.utc).isoformat(),
                extra={"n_reference": int(self.reference.shape[0]),
                       "n_current": int(cur_arr.shape[0])},
            )
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "detector": "psi",
            "last_psi": self.last_psi,
            "warn_threshold": self.warn_threshold,
            "alert_threshold": self.alert_threshold,
            "n_reference": int(self.reference.shape[0]) if self.reference is not None else 0,
            "n_current": len(self.current),
        }


# ---------------------------------------------------------------------------
# KS Drift — Kolmogorov-Smirnov two-sample test
# ---------------------------------------------------------------------------


class KSDriftDetector:
    """Sequential KS detector: compares reference to current sample.

    Emits warning when the KS test p-value drops below alpha.
    """

    def __init__(
        self,
        reference: Optional[np.ndarray] = None,
        alpha: float = DEFAULTS["ks_alpha"],
        min_current_samples: int = 30,
    ):
        self.reference: Optional[np.ndarray] = (
            np.asarray(reference, dtype=float).reshape(-1)
            if reference is not None else None
        )
        self.alpha = float(alpha)
        self.min_current_samples = int(min_current_samples)
        self.current: list[float] = []
        self.last_stat: float = 0.0
        self.last_pvalue: float = 1.0

    def set_reference(self, ref: np.ndarray) -> None:
        self.reference = np.asarray(ref, dtype=float).reshape(-1)

    def update(self, value: float) -> Optional["DriftWarning"]:
        if self.reference is None:
            return None
        self.current.append(float(value))
        if len(self.current) < self.min_current_samples:
            return None
        cur_arr = np.asarray(self.current, dtype=float)
        stat, pval = sp_stats.ks_2samp(self.reference, cur_arr)
        self.last_stat = float(stat)
        self.last_pvalue = float(pval)
        if pval < self.alpha:
            return DriftWarning(
                detector="ks",
                severity="alert" if pval < self.alpha / 10 else "warning",
                score=float(stat),
                threshold=float(1.0 - self.alpha),
                message=f"KS stat={stat:.4f}, p={pval:.4e} < alpha={self.alpha}",
                ts=datetime.now(timezone.utc).isoformat(),
                extra={"pvalue": float(pval),
                       "n_reference": int(self.reference.shape[0]),
                       "n_current": int(cur_arr.shape[0])},
            )
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "detector": "ks",
            "last_stat": self.last_stat,
            "last_pvalue": self.last_pvalue,
            "alpha": self.alpha,
            "n_reference": int(self.reference.shape[0]) if self.reference is not None else 0,
            "n_current": len(self.current),
        }


# ---------------------------------------------------------------------------
# Page-Hinkley
# ---------------------------------------------------------------------------


class PageHinkleyDetector:
    """Sequential Page-Hinkley change detector.

    Tracks the cumulative sum of deviations from the running mean, and the
    minimum of that sum. Signals drift when (PH - PH_min) > threshold.

    Args:
        threshold     : detection threshold (typical 50)
        drift_threshold: minimum magnitude before incrementing (lambda)
        min_observations: warmup before PH starts signaling
    """

    def __init__(
        self,
        threshold: float = DEFAULTS["ph_threshold"],
        drift_threshold: float = DEFAULTS["ph_drift_threshold"],
        min_observations: int = DEFAULTS["ph_min_observations"],
    ):
        self.threshold = float(threshold)
        self.drift_threshold = float(drift_threshold)
        self.min_observations = int(min_observations)
        self.n: int = 0
        self.sum_x: float = 0.0
        self.sum_x2: float = 0.0
        self.ph_sum: float = 0.0
        self.ph_min: float = 0.0
        self.last_value: float = 0.0

    def update(self, value: float) -> Optional["DriftWarning"]:
        self.n += 1
        v = float(value)
        self.sum_x += v
        self.sum_x2 += v * v
        # Running mean
        if self.n < self.min_observations:
            return None
        mean = self.sum_x / self.n
        # Increment PH sum with deviation minus drift_threshold
        self.ph_sum += (v - mean) - self.drift_threshold
        if self.ph_sum < self.ph_min:
            self.ph_min = self.ph_sum
        self.last_value = self.ph_sum - self.ph_min
        if self.last_value > self.threshold:
            return DriftWarning(
                detector="page_hinkley",
                severity="alert",
                score=float(self.last_value),
                threshold=self.threshold,
                message=f"PH={self.last_value:.2f} > threshold={self.threshold}",
                ts=datetime.now(timezone.utc).isoformat(),
                extra={"n": self.n,
                       "mean": float(mean),
                       "ph_min": float(self.ph_min)},
            )
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "detector": "page_hinkley",
            "n": self.n,
            "ph_sum": self.ph_sum,
            "ph_min": self.ph_min,
            "ph_current": self.last_value,
            "threshold": self.threshold,
            "drift_threshold": self.drift_threshold,
        }


# ---------------------------------------------------------------------------
# ADWIN — Adaptive Windowing
# ---------------------------------------------------------------------------


class ADWINDetector:
    """ADWIN (Adaptive Windowing) drift detector.

    Maintains a variable-length window of recent observations. When the
    difference in means between two sub-windows exceeds a threshold (which
    shrinks with window length), drift is signaled and the window is shrunk.

    Implementation uses the simplified variant from Bifet & Gavaldà (2007)
    with Hoeffding-bound based cut threshold.

    Args:
        delta         : confidence parameter (smaller = fewer false positives)
        min_window    : minimum window size before drift can be signaled
        max_window    : cap on window size (memory bound)
    """

    def __init__(
        self,
        delta: float = DEFAULTS["adwin_delta"],
        min_window: int = DEFAULTS["adwin_min_window"],
        max_window: int = DEFAULTS["adwin_max_window"],
    ):
        self.delta = float(delta)
        self.min_window = int(min_window)
        self.max_window = int(max_window)
        self.window: list[float] = []
        self.last_cut_size: int = 0
        self.last_mean_diff: float = 0.0
        self.last_threshold: float = 0.0

    def update(self, value: float) -> Optional["DriftWarning"]:
        v = float(value)
        self.window.append(v)
        # Trim to max window
        if len(self.window) > self.max_window:
            self.window = self.window[-self.max_window:]
        n = len(self.window)
        if n < 2 * self.min_window:
            return None
        # Try to find a cut point that yields significantly different means
        # Search from the oldest end forward (cheap O(n) check)
        for cut in range(self.min_window, n - self.min_window + 1):
            w0 = self.window[:cut]
            w1 = self.window[cut:]
            n0, n1 = len(w0), len(w1)
            if n0 < self.min_window or n1 < self.min_window:
                continue
            mean0 = float(np.mean(w0))
            mean1 = float(np.mean(w1))
            # ADWIN-1 cut threshold — uses the simplified Hoeffding bound
            # that most production implementations (e.g. `river`) actually use:
            # ε = sqrt((1 / (2 * min(n0, n1))) * ln(2 / δ))
            # The full ADWIN-2 bound uses m = 1/n0 + 1/n1, but that is too
            # conservative at moderate sample sizes to fire on realistic drift.
            n_min = min(n0, n1)
            eps = math.sqrt((1.0 / (2.0 * n_min)) * math.log(2.0 / self.delta))
            diff = abs(mean1 - mean0)
            if diff > eps:
                # Drift detected — shrink window to w1
                self.window = w1
                self.last_cut_size = cut
                self.last_mean_diff = float(diff)
                self.last_threshold = float(eps)
                return DriftWarning(
                    detector="adwin",
                    severity="alert",
                    score=float(diff),
                    threshold=float(eps),
                    message=(
                        f"ADWIN cut at {cut}: |Δmean|={diff:.4f} > ε={eps:.4f}"
                    ),
                    ts=datetime.now(timezone.utc).isoformat(),
                    extra={"n0": n0, "n1": n1,
                           "mean0": float(mean0), "mean1": float(mean1),
                           "delta": self.delta},
                )
        return None

    def stats(self) -> dict[str, Any]:
        return {
            "detector": "adwin",
            "window_size": len(self.window),
            "delta": self.delta,
            "last_cut_size": self.last_cut_size,
            "last_mean_diff": self.last_mean_diff,
            "last_threshold": self.last_threshold,
            "window_mean": float(np.mean(self.window)) if self.window else 0.0,
        }


# ---------------------------------------------------------------------------
# DriftWarning + DriftMonitor
# ---------------------------------------------------------------------------


@dataclass
class DriftWarning:
    """A drift detection event from any detector."""
    detector: str
    severity: str       # "warning" or "alert"
    score: float
    threshold: float
    message: str
    ts: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DriftMonitor:
    """Fan-out a value to all registered detectors and aggregate warnings.

    Usage:
        monitor = DriftMonitor()
        monitor.set_reference(np.array([...]))  # baseline confidence stream
        for v in live_confidences:
            warnings = monitor.update(v)
            for w in warnings:
                print(w.detector, w.severity, w.message)
    """

    def __init__(
        self,
        detectors: Optional[list[Any]] = None,
        warnings_dir: str = DEFAULTS["warnings_dir"],
        config: Optional[dict] = None,
    ):
        if detectors is None:
            detectors = [
                PSIDetector(),
                KSDriftDetector(),
                PageHinkleyDetector(),
                ADWINDetector(),
            ]
        self.detectors = detectors
        self.warnings_dir = warnings_dir
        self.cfg = {**DEFAULTS, **(config or {})}
        self._warning_count: int = 0
        self._alert_count: int = 0
        self._last_warnings: list[DriftWarning] = []

    def set_reference(self, reference: np.ndarray) -> None:
        ref = np.asarray(reference, dtype=float).reshape(-1)
        for d in self.detectors:
            if hasattr(d, "set_reference"):
                d.set_reference(ref)

    def update(self, value: float) -> list[DriftWarning]:
        warnings: list[DriftWarning] = []
        for d in self.detectors:
            try:
                w = d.update(value)
            except Exception as e:
                log.debug("detector %s failed: %s", type(d).__name__, e)
                continue
            if w is not None:
                warnings.append(w)
                self._warning_count += 1
                if w.severity == "alert":
                    self._alert_count += 1
                self._persist_warning(w)
        if warnings:
            self._last_warnings = warnings
        return warnings

    def stats(self) -> dict[str, Any]:
        return {
            "n_detectors": len(self.detectors),
            "total_warnings": self._warning_count,
            "total_alerts": self._alert_count,
            "detectors": [d.stats() for d in self.detectors],
            "last_warnings": [w.to_dict() for w in self._last_warnings],
        }

    def _persist_warning(self, w: DriftWarning) -> None:
        try:
            out_dir = Path(self.warnings_dir)
            out_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = out_dir / f"warnings_{day}.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(w.to_dict(), default=str) + "\n")
        except Exception as e:
            log.warning("failed to persist drift warning: %s", e)


__all__ = [
    "psi_score",
    "PSIDetector",
    "KSDriftDetector",
    "PageHinkleyDetector",
    "ADWINDetector",
    "DriftWarning",
    "DriftMonitor",
    "DEFAULTS",
]
