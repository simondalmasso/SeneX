"""
SENECIO ORACLE — ACT XXVII Priority 5: Explainability
======================================================

Provides per-prediction feature attributions using SHAP when available, with
a permutation-importance fallback that exposes the same API so the module
is always functional regardless of whether `shap` is installed.

Functionality:
  - fit_explainer(X, y, feature_names, model_type)
      Fits a model + explainer. Tree models get TreeExplainer (fast, exact);
      other models fall back to KernelExplainer or permutation importance.
  - explain_one(X_row)
      Returns top-K contributing features with attribution values (signed).
  - explain_batch(X_rows)
      Returns attributions for every row (used for batch analytics).
  - feature_importance_history()
      Returns the rolling history of feature importances — feeds the
      Research Metrics module's `feature_stability()` function.

Persistence:
  Each explainer fit produces a JSON record under
  `data/research/explainers/` with the feature importances at fit time.
  Per-prediction attributions are persisted under
  `data/research/attributions/` (one JSONL per day).

This module is STRICT_ADDITIVE — it does NOT touch prediction_model /
feature_engineering / signal_generation / verifier. It trains its own
interpretable surrogate model on the (features, outcome) pairs that the
verifier already produces, then explains the surrogate's predictions.
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

log = logging.getLogger("senecio.research.explainability")


DEFAULTS: dict[str, Any] = {
    "explainers_dir":       "data/research/explainers",
    "attributions_dir":     "data/research/attributions",
    "default_model_type":   "tree",         # "tree" | "logistic" | "forest"
    "top_k_features":       10,
    "min_samples_to_fit":   100,
    # Whether to use shap if available
    "prefer_shap":          True,
}


# ---------------------------------------------------------------------------
# Explainer result types
# ---------------------------------------------------------------------------


@dataclass
class Attribution:
    """Per-feature attribution for a single prediction."""
    feature_name: str
    feature_value: float
    attribution: float          # signed contribution to the prediction
    abs_attribution: float
    rank: int                   # 1-indexed rank by |attribution|

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PredictionExplanation:
    """Full explanation for one prediction."""
    base_value: float                   # expected prediction over training set
    model_output: float                 # the surrogate's prediction for this row
    top_attributions: list[Attribution]
    all_attributions: list[Attribution]
    explained_at: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_value": self.base_value,
            "model_output": self.model_output,
            "top_attributions": [a.to_dict() for a in self.top_attributions],
            "all_attributions": [a.to_dict() for a in self.all_attributions],
            "explained_at": self.explained_at,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# Explainer wrapper
# ---------------------------------------------------------------------------


class Explainer:
    """Wraps a fitted model + attribution engine.

    The model is fit on (X, y) — the oracle's historical features and
    outcomes. The attribution engine produces per-row feature attributions
    using either SHAP (preferred for tree models) or permutation importance
    (model-agnostic fallback).

    The fitted explainer is independent of the production prediction model —
    it's a research surrogate used only for explainability.
    """

    def __init__(
        self,
        feature_names: Optional[list[str]] = None,
        model_type: str = DEFAULTS["default_model_type"],
        top_k: int = DEFAULTS["top_k_features"],
        prefer_shap: bool = DEFAULTS["prefer_shap"],
    ):
        self.feature_names: list[str] = list(feature_names) if feature_names else []
        self.model_type = (model_type or "tree").lower()
        self.top_k = int(top_k)
        self.prefer_shap = bool(prefer_shap)

        # Fitted state
        self._model = None
        self._explainer_kind: str = "none"   # "shap_tree", "shap_kernel", "permutation"
        self._shap_explainer = None
        self._base_value: float = 0.0
        self._training_mean: Optional[np.ndarray] = None
        self._n_fit: int = 0
        self._feature_importance: Optional[np.ndarray] = None

    # -------- fit --------

    def fit(
        self,
        X: np.ndarray,
        y: np.ndarray,
        feature_names: Optional[list[str]] = None,
    ) -> "Explainer":
        """Fit the surrogate model + attribution engine.

        Args:
            X              : (n_samples, n_features) feature matrix
            y              : (n_samples,) binary 0/1 target
            feature_names  : optional names (overrides constructor)
        """
        X_arr = np.asarray(X, dtype=float)
        if X_arr.ndim == 1:
            X_arr = X_arr.reshape(-1, 1)
        y_arr = np.asarray(y, dtype=float).reshape(-1)
        n, n_feat = X_arr.shape
        if feature_names is not None:
            self.feature_names = list(feature_names)
        if not self.feature_names:
            self.feature_names = [f"f{i}" for i in range(n_feat)]
        if n < DEFAULTS["min_samples_to_fit"]:
            log.warning(
                "explainer fit with %d samples (< %d) — results may be unreliable",
                n, DEFAULTS["min_samples_to_fit"],
            )
        # Replace NaN/inf with 0 for sklearn
        X_arr = np.nan_to_num(X_arr, nan=0.0, posinf=0.0, neginf=0.0)

        # Pick model
        self._model = self._build_model(self.model_type)
        try:
            self._model.fit(X_arr, y_arr.astype(int))
        except Exception as e:
            log.warning("model fit failed (%s) — falling back to logistic", e)
            self._model = self._build_model("logistic")
            self._model.fit(X_arr, y_arr.astype(int))

        # Compute base value (mean prediction on training set)
        try:
            if hasattr(self._model, "predict_proba"):
                preds = self._model.predict_proba(X_arr)[:, -1]
            else:
                preds = self._model.predict(X_arr).astype(float)
            self._base_value = float(np.mean(preds))
        except Exception:
            self._base_value = float(np.mean(y_arr))
        self._training_mean = X_arr.mean(axis=0)
        self._n_fit = int(n)

        # Try SHAP first if preferred and available
        if self.prefer_shap:
            self._try_init_shap(X_arr)

        # If SHAP unavailable, compute permutation importance as fallback
        if self._explainer_kind == "none":
            self._init_permutation_importance(X_arr, y_arr)

        # Persist this fit's feature importance snapshot
        self._persist_fit_snapshot()
        return self

    def _build_model(self, model_type: str):
        from sklearn.tree import DecisionTreeClassifier
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import StandardScaler
        mt = (model_type or "").lower()
        if mt in ("tree", "decision_tree"):
            return DecisionTreeClassifier(max_depth=5, random_state=42,
                                          class_weight="balanced")
        if mt in ("forest", "random_forest"):
            return RandomForestClassifier(
                n_estimators=80, max_depth=6, random_state=42,
                class_weight="balanced", n_jobs=-1,
            )
        if mt in ("logistic", "logistic_regression"):
            return Pipeline([
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(max_iter=1000,
                                            class_weight="balanced")),
            ])
        # Default to tree
        return DecisionTreeClassifier(max_depth=5, random_state=42,
                                      class_weight="balanced")

    def _try_init_shap(self, X_arr: np.ndarray) -> None:
        try:
            import shap  # noqa: F401
        except ImportError:
            self._explainer_kind = "none"
            return
        try:
            import shap
            # TreeExplainer for tree-based models (fast, exact)
            if self.model_type in ("tree", "forest") and hasattr(self._model, "tree_"):
                self._shap_explainer = shap.TreeExplainer(self._model)
                self._explainer_kind = "shap_tree"
                log.info("SHAP TreeExplainer initialized")
                return
            # KernelExplainer for other models (slower, model-agnostic)
            # Use training mean as background (small dataset for speed)
            bg = shap.kmeans(X_arr, min(50, X_arr.shape[0]))
            self._shap_explainer = shap.KernelExplainer(
                self._predict_proba_for_shap, bg,
            )
            self._explainer_kind = "shap_kernel"
            log.info("SHAP KernelExplainer initialized")
        except Exception as e:
            log.warning("SHAP init failed (%s) — falling back to permutation", e)
            self._explainer_kind = "none"
            self._shap_explainer = None

    def _predict_proba_for_shap(self, X_arr: np.ndarray) -> np.ndarray:
        """predict_proba wrapper for KernelExplainer (returns class-1 prob)."""
        if hasattr(self._model, "predict_proba"):
            return self._model.predict_proba(X_arr)[:, -1]
        return self._model.predict(X_arr).astype(float)

    def _init_permutation_importance(
        self, X_arr: np.ndarray, y_arr: np.ndarray,
    ) -> None:
        from sklearn.inspection import permutation_importance
        try:
            result = permutation_importance(
                self._model, X_arr, y_arr.astype(int),
                n_repeats=5, random_state=42, scoring="roc_auc",
            )
            self._feature_importance = np.asarray(
                result.importances_mean, dtype=float,
            )
            self._explainer_kind = "permutation"
            log.info("Permutation importance initialized (fallback for SHAP)")
        except Exception as e:
            log.warning("permutation importance failed (%s) — using tree importance", e)
            self._feature_importance = self._fallback_tree_importance()
            self._explainer_kind = "tree_importance"

    def _fallback_tree_importance(self) -> np.ndarray:
        if hasattr(self._model, "feature_importances_"):
            return np.asarray(self._model.feature_importances_, dtype=float)
        # For pipeline / logistic — use |coef| as importance
        try:
            clf = getattr(self._model, "named_steps", {}).get("clf", self._model)
            if hasattr(clf, "coef_"):
                return np.abs(clf.coef_[0])
        except Exception:
            pass
        return np.ones(len(self.feature_names), dtype=float)

    # -------- explain --------

    def explain_one(self, X_row: np.ndarray) -> PredictionExplanation:
        """Compute per-feature attributions for a single row."""
        if self._model is None:
            raise RuntimeError("explainer not fitted — call fit() first")
        row = np.asarray(X_row, dtype=float).reshape(1, -1)
        if row.shape[1] != len(self.feature_names):
            raise ValueError(
                f"X_row has {row.shape[1]} features but explainer was fit on {len(self.feature_names)}"
            )
        row = np.nan_to_num(row, nan=0.0, posinf=0.0, neginf=0.0)
        # Model output
        try:
            if hasattr(self._model, "predict_proba"):
                model_out = float(self._model.predict_proba(row)[0, -1])
            else:
                model_out = float(self._model.predict(row)[0])
        except Exception as e:
            log.warning("model predict failed in explain_one: %s", e)
            model_out = float("nan")

        # Per-feature attributions
        attributions = self._compute_attributions(row)
        # Sort by |attribution| desc
        sorted_idx = sorted(
            range(len(attributions)),
            key=lambda i: abs(attributions[i]),
            reverse=True,
        )
        all_attr: list[Attribution] = []
        for rank, idx in enumerate(sorted_idx, start=1):
            all_attr.append(Attribution(
                feature_name=self.feature_names[idx],
                feature_value=float(row[0, idx]),
                attribution=float(attributions[idx]),
                abs_attribution=float(abs(attributions[idx])),
                rank=rank,
            ))
        top_k = min(self.top_k, len(all_attr))
        return PredictionExplanation(
            base_value=self._base_value,
            model_output=model_out,
            top_attributions=all_attr[:top_k],
            all_attributions=all_attr,
            explained_at=datetime.now(timezone.utc).isoformat(),
            extra={
                "explainer_kind": self._explainer_kind,
                "model_type": self.model_type,
                "n_fit": self._n_fit,
            },
        )

    def explain_batch(self, X_rows: np.ndarray) -> list[PredictionExplanation]:
        X = np.asarray(X_rows, dtype=float)
        if X.ndim == 1:
            X = X.reshape(1, -1)
        return [self.explain_one(X[i]) for i in range(X.shape[0])]

    def _compute_attributions(self, row: np.ndarray) -> np.ndarray:
        """Return (n_features,) array of signed attributions."""
        n_feat = len(self.feature_names)
        # SHAP path
        if self._explainer_kind in ("shap_tree", "shap_kernel") and self._shap_explainer is not None:
            try:
                shap_values = self._shap_explainer.shap_values(row)
                # SHAP returns list [class0, class1] for tree classifiers
                if isinstance(shap_values, list):
                    sv = np.asarray(shap_values[-1], dtype=float)
                else:
                    sv = np.asarray(shap_values, dtype=float)
                if sv.ndim == 3:
                    sv = sv[0, :, -1]   # (sample, feat, class) → (feat,)
                elif sv.ndim == 2:
                    sv = sv[0]
                return sv.reshape(-1)
            except Exception as e:
                log.warning("SHAP explain failed (%s) — falling back", e)

        # Permutation importance fallback — distribute importance proportional
        # to (feature_value - training_mean), signed by feature importance × sign
        if self._feature_importance is None:
            self._feature_importance = self._fallback_tree_importance()
        importance = self._feature_importance
        deviations = row[0] - self._training_mean
        # Heuristic attribution: importance × deviation × sign
        # (positive feature importance × positive deviation → positive attribution)
        attr = importance * deviations
        return attr

    # -------- history / persistence --------

    def feature_importance_history(
        self, history_dir: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Load all historical feature-importance snapshots from disk.

        Each snapshot was persisted at fit() time. Returns a list of dicts:
            [{"fitted_at": ..., "importances": {feat_name: val, ...}}, ...]
        """
        h_dir = Path(history_dir or DEFAULTS["explainers_dir"])
        if not h_dir.exists():
            return []
        out: list[dict[str, Any]] = []
        for fp in sorted(h_dir.glob("explainer_*.json")):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    out.append(json.load(f))
            except Exception:
                continue
        return out

    def _persist_fit_snapshot(self) -> None:
        try:
            out_dir = Path(DEFAULTS["explainers_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            path = out_dir / f"explainer_{ts}.json"
            importance = (
                self._feature_importance
                if self._feature_importance is not None
                else self._fallback_tree_importance()
            )
            snapshot = {
                "fitted_at": datetime.now(timezone.utc).isoformat(),
                "model_type": self.model_type,
                "explainer_kind": self._explainer_kind,
                "n_fit": self._n_fit,
                "feature_names": list(self.feature_names),
                "importances": {
                    name: float(importance[i]) if i < len(importance) else 0.0
                    for i, name in enumerate(self.feature_names)
                },
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, indent=2)
            log.info("explainer snapshot persisted to %s", path)
        except Exception as e:
            log.warning("failed to persist explainer snapshot: %s", e)

    def persist_attribution(
        self, explanation: PredictionExplanation,
        prediction_id: Optional[Any] = None,
        attributions_dir: Optional[str] = None,
    ) -> None:
        """Append a per-prediction attribution as JSONL."""
        try:
            out_dir = Path(attributions_dir or DEFAULTS["attributions_dir"])
            out_dir.mkdir(parents=True, exist_ok=True)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = out_dir / f"attributions_{day}.jsonl"
            record = explanation.to_dict()
            record["prediction_id"] = prediction_id
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            log.warning("failed to persist attribution: %s", e)

    # -------- introspection --------

    def stats(self) -> dict[str, Any]:
        return {
            "fitted": self._model is not None,
            "model_type": self.model_type,
            "explainer_kind": self._explainer_kind,
            "n_features": len(self.feature_names),
            "feature_names": list(self.feature_names),
            "n_fit": self._n_fit,
            "base_value": self._base_value,
            "prefer_shap": self.prefer_shap,
        }


# ---------------------------------------------------------------------------
# Top-level convenience
# ---------------------------------------------------------------------------


def fit_explainer(
    X: np.ndarray,
    y: np.ndarray,
    feature_names: Optional[list[str]] = None,
    model_type: str = DEFAULTS["default_model_type"],
    prefer_shap: bool = DEFAULTS["prefer_shap"],
    top_k: int = DEFAULTS["top_k_features"],
) -> Explainer:
    """Build + fit an Explainer in one call."""
    e = Explainer(
        feature_names=feature_names,
        model_type=model_type,
        top_k=top_k,
        prefer_shap=prefer_shap,
    )
    return e.fit(X, y, feature_names=feature_names)


__all__ = [
    "Attribution",
    "PredictionExplanation",
    "Explainer",
    "fit_explainer",
    "DEFAULTS",
]
