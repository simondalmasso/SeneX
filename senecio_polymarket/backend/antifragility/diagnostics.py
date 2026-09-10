"""
ACT-XXIX — Module 4: Self-Diagnostics, Health Scoring & Anomaly Detection
==========================================================================

Continuous self-validation: monitors every subsystem's health, decomposes
prediction confidence into feature contributions, clusters anomalies, and
detects ensemble disagreement.

Public surface
--------------
- ``MetricSample``                  — (name, value, ts, unit, tags)
- ``HealthScorer``                  — weighted multi-metric health score
- ``ConfidenceDecomposer``          — decompose prediction confidence into
                                       contributions from each feature
- ``AnomalyClusterer``              — online k-means anomaly clustering
- ``EnsembleDisagreementDetector``  — track inter-prediction variance
- ``SelfDiagnostics``               — orchestrator combining all four
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

import numpy as np


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# MetricSample
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    ts: str
    unit: str = ""
    tags: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# HealthScorer — weighted multi-metric health score
# ---------------------------------------------------------------------------

class HealthScorer:
    """Computes a composite health score from named metrics.

    Each metric has:
      - name
      - getter: () -> float (latest value)
      - weight: float (relative importance)
      - transform: 'higher_better' | 'lower_better' | 'in_range'
      - bounds: (low, high) for normalisation

    Score is computed as:
        score = sum(weight_i * normalised(value_i)) / sum(weight_i)
    where normalised maps each value to [0, 1] based on bounds + transform.
    """

    TRANSFORMS = ("higher_better", "lower_better", "in_range")

    def __init__(self, name: str = "default"):
        self.name = name
        self._metrics: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._history: deque[dict] = deque(maxlen=1000)

    def register_metric(self, name: str, getter: Callable[[], float],
                        weight: float = 1.0,
                        transform: str = "higher_better",
                        bounds: tuple[float, float] = (0.0, 1.0),
                        unit: str = "") -> None:
        if transform not in self.TRANSFORMS:
            raise ValueError(f"transform must be one of {self.TRANSFORMS}")
        if weight < 0:
            raise ValueError("weight must be >= 0")
        with self._lock:
            self._metrics[name] = {
                "getter": getter,
                "weight": weight,
                "transform": transform,
                "bounds": bounds,
                "unit": unit,
            }

    def unregister_metric(self, name: str) -> bool:
        with self._lock:
            return self._metrics.pop(name, None) is not None

    @staticmethod
    def _normalise(value: float, transform: str,
                   bounds: tuple[float, float]) -> float:
        low, high = bounds
        if high <= low:
            return 0.0
        # Clamp to bounds first
        clamped = max(low, min(high, value))
        ratio = (clamped - low) / (high - low)
        if transform == "higher_better":
            return ratio
        elif transform == "lower_better":
            return 1.0 - ratio
        else:  # in_range
            # Bell curve centred at midpoint
            mid = (low + high) / 2
            half = (high - low) / 2
            if half <= 0:
                return 0.0
            sigma = half / 3  # 3-sigma at the bounds
            return math.exp(-((value - mid) ** 2) / (2 * sigma ** 2))

    def compute(self) -> dict:
        """Returns {score, components, ts, ok}."""
        with self._lock:
            metrics = list(self._metrics.items())
        components = []
        total_w = 0.0
        total_score = 0.0
        for name, cfg in metrics:
            try:
                value = float(cfg["getter"]())
            except Exception:
                value = float("nan")
                norm = 0.0
            else:
                norm = self._normalise(value, cfg["transform"], cfg["bounds"])
            if not math.isnan(value):
                total_w += cfg["weight"]
                total_score += cfg["weight"] * norm
            components.append({
                "name": name,
                "value": value,
                "weight": cfg["weight"],
                "transform": cfg["transform"],
                "bounds": list(cfg["bounds"]),
                "normalised": norm,
                "unit": cfg["unit"],
            })
        score = (total_score / total_w) if total_w > 0 else 0.0
        result = {
            "name": self.name,
            "score": round(score, 6),
            "components": components,
            "ts": _now_iso(),
            "ok": score >= 0.5,
        }
        with self._lock:
            self._history.append({"ts": result["ts"], "score": result["score"]})
        return result

    def history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._history)[-limit:]


# ---------------------------------------------------------------------------
# ConfidenceDecomposer — feature contributions to prediction confidence
# ---------------------------------------------------------------------------

class ConfidenceDecomposer:
    """Decomposes a prediction's confidence into per-feature contributions.

    Three decomposition methods supported:
      - 'weight'      — use stored weights (from SingleDecisionCore.weights)
      - 'shapley'     — simplified Shapley via marginal removal
      - 'correlation' — feature × outcome correlation (rolling)

    Method 'shapley' is exact for small feature sets (<=8 features) and
    approximate for larger ones (sampled permutations).
    """

    def __init__(self, method: str = "weight"):
        if method not in ("weight", "shapley", "correlation"):
            raise ValueError(f"unknown method: {method}")
        self.method = method
        self._weights: dict[str, float] = {}
        self._predictor: Callable[[dict], float] | None = None
        self._corr_window: deque[tuple[dict, float]] = deque(maxlen=500)
        self._lock = threading.Lock()

    def set_weights(self, weights: dict[str, float]) -> None:
        with self._lock:
            self._weights = dict(weights)

    def set_predictor(self, predictor: Callable[[dict], float]) -> None:
        """For Shapley method: predictor(features_dict) -> confidence."""
        with self._lock:
            self._predictor = predictor

    def observe(self, features: dict, outcome: float) -> None:
        """For correlation method: record (features, outcome) pair."""
        with self._lock:
            self._corr_window.append((dict(features), float(outcome)))

    def decompose(self, features: dict,
                  baseline_confidence: float | None = None) -> dict:
        """Returns {total, contributions: {feature: contribution}, method}."""
        with self._lock:
            method = self.method
            weights = dict(self._weights)
            predictor = self._predictor
            corr_window = list(self._corr_window)
        if method == "weight":
            return self._decompose_weight(features, weights, baseline_confidence)
        elif method == "shapley":
            return self._decompose_shapley(features, predictor,
                                           baseline_confidence)
        else:
            return self._decompose_correlation(features, corr_window,
                                               baseline_confidence)

    @staticmethod
    def _decompose_weight(features: dict, weights: dict[str, float],
                          baseline: float | None) -> dict:
        # Contribution = normalised feature_value × feature_weight
        contributions = {}
        total_w = sum(abs(w) for w in weights.values()) or 1.0
        for fname, fval in features.items():
            try:
                v = float(fval)
            except (TypeError, ValueError):
                continue
            w = weights.get(fname, 0.0)
            # Scale feature to [-1, 1] roughly (assume normalised)
            v_signed = max(-1.0, min(1.0, v))
            contributions[fname] = (w / total_w) * v_signed
        total = sum(contributions.values())
        if baseline is not None:
            # Rescale contributions to match baseline
            if abs(total) > 1e-9:
                scale = baseline / total
                contributions = {k: v * scale for k, v in contributions.items()}
                total = baseline
        return {
            "method": "weight",
            "total": total,
            "contributions": contributions,
            "feature_count": len(contributions),
        }

    @staticmethod
    def _decompose_shapley(features: dict,
                           predictor: Callable[[dict], float] | None,
                           baseline: float | None,
                           max_features: int = 8,
                           n_samples: int = 100) -> dict:
        if predictor is None:
            return {"method": "shapley", "total": 0.0,
                    "contributions": {}, "error": "no predictor set"}
        feature_names = list(features.keys())[:max_features]
        if not feature_names:
            return {"method": "shapley", "total": 0.0, "contributions": {}}
        # Compute baseline (empty features) prediction
        try:
            empty_pred = float(predictor({}))
        except Exception:
            empty_pred = 0.0
        try:
            full_pred = float(predictor(dict(features)))
        except Exception:
            full_pred = baseline or 0.0
        contributions = {fname: 0.0 for fname in feature_names}
        # For small sets: exact Shapley (n! permutations)
        if len(feature_names) <= 6:
            from itertools import permutations
            perms = list(permutations(feature_names))
            for perm in perms:
                cur_features: dict = {}
                cur_pred = empty_pred
                for fname in perm:
                    cur_features[fname] = features[fname]
                    try:
                        new_pred = float(predictor(dict(cur_features)))
                    except Exception:
                        new_pred = cur_pred
                    contributions[fname] += (new_pred - cur_pred)
                    cur_pred = new_pred
            n = len(perms)
            contributions = {k: v / n for k, v in contributions.items()}
        else:
            # Sampled permutations
            rng = np.random.default_rng(42)
            for _ in range(n_samples):
                perm = list(feature_names)
                rng.shuffle(perm)
                cur_features = {}
                cur_pred = empty_pred
                for fname in perm:
                    cur_features[fname] = features[fname]
                    try:
                        new_pred = float(predictor(dict(cur_features)))
                    except Exception:
                        new_pred = cur_pred
                    contributions[fname] += (new_pred - cur_pred)
                    cur_pred = new_pred
            contributions = {k: v / n_samples for k, v in contributions.items()}
        total = sum(contributions.values())
        return {
            "method": "shapley",
            "total": total,
            "empty_baseline": empty_pred,
            "full_prediction": full_pred,
            "contributions": contributions,
            "feature_count": len(contributions),
        }

    @staticmethod
    def _decompose_correlation(features: dict,
                               corr_window: list[tuple[dict, float]],
                               baseline: float | None) -> dict:
        if not corr_window:
            return {"method": "correlation", "total": 0.0,
                    "contributions": {}, "error": "no observations"}
        # Compute Pearson correlation per feature
        contributions = {}
        feature_names = list(features.keys())
        for fname in feature_names:
            xs = []
            ys = []
            for feats, out in corr_window:
                if fname in feats:
                    try:
                        xs.append(float(feats[fname]))
                        ys.append(float(out))
                    except (TypeError, ValueError):
                        continue
            if len(xs) < 5:
                contributions[fname] = 0.0
                continue
            x_arr = np.array(xs)
            y_arr = np.array(ys)
            if x_arr.std() < 1e-9 or y_arr.std() < 1e-9:
                contributions[fname] = 0.0
                continue
            corr = float(np.corrcoef(x_arr, y_arr)[0, 1])
            # Contribution = corr × feature_value (normalised)
            try:
                v = float(features[fname])
            except (TypeError, ValueError):
                v = 0.0
            contributions[fname] = corr * max(-1.0, min(1.0, v))
        total = sum(contributions.values())
        if baseline is not None and abs(total) > 1e-9:
            scale = baseline / total
            contributions = {k: v * scale for k, v in contributions.items()}
            total = baseline
        return {
            "method": "correlation",
            "total": total,
            "contributions": contributions,
            "feature_count": len(contributions),
            "observation_count": len(corr_window),
        }


# ---------------------------------------------------------------------------
# AnomalyClusterer — online k-means anomaly clustering
# ---------------------------------------------------------------------------

class AnomalyClusterer:
    """Online k-means clustering for anomaly detection.

    - Fits incrementally as new samples arrive.
    - Anomaly score = distance to nearest centroid, normalised by the
      cluster's running std-dev.
    - Cluster labels allow grouping similar anomalies together.
    """

    def __init__(self, n_clusters: int = 5, n_features: int = 8,
                 learning_rate: float = 0.05, seed: int = 42):
        self.n_clusters = n_clusters
        self.n_features = n_features
        self.lr = learning_rate
        self.rng = np.random.default_rng(seed)
        self._centroids: np.ndarray | None = None
        self._counts: np.ndarray = np.zeros(n_clusters, dtype=np.int64)
        self._variances: np.ndarray = np.zeros((n_clusters, n_features))
        self._total_seen = 0
        self._lock = threading.Lock()
        self._anomaly_history: deque[dict] = deque(maxlen=500)
        self._anomaly_threshold = 3.0  # 3 sigma

    def _init_centroids(self) -> None:
        # Small random init around zero
        self._centroids = self.rng.normal(0, 0.1, (self.n_clusters, self.n_features))

    def partial_fit(self, x: np.ndarray | list[float]) -> int:
        """Update model with one sample. Returns assigned cluster."""
        x_arr = np.asarray(x, dtype=np.float64).flatten()
        if x_arr.size != self.n_features:
            raise ValueError(
                f"expected {self.n_features} features, got {x_arr.size}")
        with self._lock:
            if self._centroids is None:
                self._init_centroids()
            # Find nearest centroid
            dists = np.linalg.norm(self._centroids - x_arr, axis=1)
            nearest = int(np.argmin(dists))
            # Update centroid (online k-means)
            self._centroids[nearest] = (1 - self.lr) * self._centroids[nearest] + \
                                        self.lr * x_arr
            # Update running variance (exponential moving)
            delta = x_arr - self._centroids[nearest]
            self._variances[nearest] = (1 - self.lr) * self._variances[nearest] + \
                                        self.lr * delta * delta
            self._counts[nearest] += 1
            self._total_seen += 1
            return nearest

    def score(self, x: np.ndarray | list[float]) -> dict:
        """Score one sample without updating the model."""
        x_arr = np.asarray(x, dtype=np.float64).flatten()
        if x_arr.size != self.n_features:
            raise ValueError(
                f"expected {self.n_features} features, got {x_arr.size}")
        with self._lock:
            if self._centroids is None:
                self._init_centroids()
            dists = np.linalg.norm(self._centroids - x_arr, axis=1)
            nearest = int(np.argmin(dists))
            # Normalise by cluster's average variance
            avg_var = float(np.mean(self._variances[nearest])) or 1.0
            std = math.sqrt(max(avg_var, 1e-9))
            z_score = float(dists[nearest] / std)
            is_anomaly = z_score > self._anomaly_threshold
            if is_anomaly:
                self._anomaly_history.append({
                    "ts": _now_iso(),
                    "cluster": nearest,
                    "z_score": z_score,
                    "distance": float(dists[nearest]),
                })
            return {
                "cluster": nearest,
                "distance": float(dists[nearest]),
                "z_score": z_score,
                "is_anomaly": is_anomaly,
                "threshold": self._anomaly_threshold,
                "cluster_count": int(self._counts[nearest]),
                "total_seen": self._total_seen,
            }

    def fit_and_score(self, x: np.ndarray | list[float]) -> dict:
        """Update model AND return anomaly score."""
        cluster = self.partial_fit(x)
        return self.score(x)

    def cluster_summary(self) -> list[dict]:
        with self._lock:
            if self._centroids is None:
                return []
            out = []
            for i in range(self.n_clusters):
                out.append({
                    "cluster": i,
                    "count": int(self._counts[i]),
                    "centroid": self._centroids[i].tolist(),
                    "avg_variance": float(np.mean(self._variances[i])),
                })
            return out

    def anomaly_history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._anomaly_history)[-limit:]


# ---------------------------------------------------------------------------
# EnsembleDisagreementDetector — track inter-prediction variance
# ---------------------------------------------------------------------------

class EnsembleDisagreementDetector:
    """Detects when ensemble members disagree significantly.

    Each ensemble member predicts a value (e.g. confidence or expected return).
    The detector computes the spread (std-dev / range) and fires when it
    exceeds a threshold.

    Also tracks:
      - Per-member bias vs realised outcomes
      - Cross-member correlation
      - Disagreement history (for trend analysis)
    """

    def __init__(self, n_members: int = 3,
                 disagreement_threshold: float = 0.3,
                 window_size: int = 200):
        self.n_members = n_members
        self.threshold = disagreement_threshold
        self.window_size = window_size
        self._member_history: deque[list[float]] = deque(maxlen=window_size)
        self._outcome_history: deque[float] = deque(maxlen=window_size)
        self._member_bias: list[float] = [0.0] * n_members
        self._member_count: int = 0
        self._disagreement_history: deque[dict] = deque(maxlen=500)
        self._lock = threading.Lock()

    def record(self, member_predictions: list[float],
               outcome: float | None = None) -> dict:
        """Record one prediction across all members.

        Returns the disagreement snapshot for this prediction.
        """
        if len(member_predictions) != self.n_members:
            raise ValueError(
                f"expected {self.n_members} predictions, "
                f"got {len(member_predictions)}")
        with self._lock:
            preds = [float(p) for p in member_predictions]
            self._member_history.append(preds)
            if outcome is not None:
                self._outcome_history.append(float(outcome))
                # Update bias estimates
                for i, p in enumerate(preds):
                    err = float(outcome) - p
                    self._member_bias[i] = (
                        (self._member_bias[i] * self._member_count + err) /
                        (self._member_count + 1)
                    )
                self._member_count += 1
            mean_pred = float(sum(preds) / len(preds))
            std_pred = float(np.std(preds))
            range_pred = max(preds) - min(preds)
            spread = (std_pred / abs(mean_pred)) if abs(mean_pred) > 1e-9 else std_pred
            disagreement = spread > self.threshold
            snapshot = {
                "ts": _now_iso(),
                "members": preds,
                "mean": mean_pred,
                "std": std_pred,
                "range": float(range_pred),
                "spread": spread,
                "disagreement": disagreement,
                "threshold": self.threshold,
            }
            if disagreement:
                self._disagreement_history.append(snapshot)
            return snapshot

    def member_bias(self) -> list[dict]:
        with self._lock:
            return [
                {"member": i, "bias": b, "samples": self._member_count}
                for i, b in enumerate(self._member_bias)
            ]

    def cross_correlation(self) -> np.ndarray:
        """Pearson correlation matrix between members (last window)."""
        with self._lock:
            if len(self._member_history) < 5:
                return np.eye(self.n_members)
            arr = np.array(list(self._member_history))
            return np.corrcoef(arr.T)

    def disagreement_history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._disagreement_history)[-limit:]

    def summary(self) -> dict:
        with self._lock:
            return {
                "n_members": self.n_members,
                "samples": len(self._member_history),
                "samples_with_outcome": self._member_count,
                "member_bias": list(self._member_bias),
                "disagreement_count": len(self._disagreement_history),
                "threshold": self.threshold,
            }


# ---------------------------------------------------------------------------
# SelfDiagnostics — orchestrator
# ---------------------------------------------------------------------------

class SelfDiagnostics:
    """Combines HealthScorer, ConfidenceDecomposer, AnomalyClusterer,
    and EnsembleDisagreementDetector into one orchestrator.

    Provides a single ``run()`` method that returns a full diagnostic snapshot.
    """

    def __init__(self, name: str = "self_diagnostics"):
        self.name = name
        self.health = HealthScorer(name=f"{name}_health")
        self.confidence = ConfidenceDecomposer(method="weight")
        self.anomalies = AnomalyClusterer(n_clusters=5, n_features=8)
        self.ensemble = EnsembleDisagreementDetector(n_members=3)
        self._lock = threading.Lock()
        self._history: deque[dict] = deque(maxlen=200)

    def run(self, features: dict | None = None,
            member_predictions: list[float] | None = None,
            sample_vector: list[float] | None = None) -> dict:
        result = {
            "ts": _now_iso(),
            "name": self.name,
            "health": self.health.compute(),
        }
        if features is not None:
            result["confidence_decomposition"] = self.confidence.decompose(features)
        if member_predictions is not None:
            result["ensemble"] = self.ensemble.record(member_predictions)
        if sample_vector is not None:
            result["anomaly"] = self.anomalies.fit_and_score(sample_vector)
        with self._lock:
            self._history.append({"ts": result["ts"],
                                  "health_score": result["health"]["score"]})
        return result

    def history(self, limit: int = 100) -> list[dict]:
        with self._lock:
            return list(self._history)[-limit:]


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "MetricSample",
    "HealthScorer",
    "ConfidenceDecomposer",
    "AnomalyClusterer",
    "EnsembleDisagreementDetector",
    "SelfDiagnostics",
]
