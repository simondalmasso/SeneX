from __future__ import annotations

import math
from typing import Any, Iterable

from . import common


class EvaluationContractError(ValueError):
    """Historical/synthetic evaluation contract violation."""


def _aligned(
    labels: Iterable[int],
    p_market: Iterable[float],
    p_candidate: Iterable[float],
) -> tuple[list[int], list[float], list[float]]:
    ys = [common.binary_label(value) for value in labels]
    market = [common.probability(value, name="p_market") for value in p_market]
    candidate = [
        common.probability(value, name="candidate_probability")
        for value in p_candidate
    ]
    if not ys or not (len(ys) == len(market) == len(candidate)):
        raise EvaluationContractError("labels and probabilities must be non-empty and aligned")
    return ys, market, candidate


def compare_to_market(
    labels: Iterable[int],
    p_market: Iterable[float],
    p_candidate: Iterable[float],
) -> dict[str, float]:
    """Compare a candidate with the contemporaneous market baseline."""
    ys, market, candidate = _aligned(labels, p_market, p_candidate)
    market_brier = common.brier_score(ys, market)
    candidate_brier = common.brier_score(ys, candidate)
    market_log_loss = common.log_loss(ys, market)
    candidate_log_loss = common.log_loss(ys, candidate)
    return {
        "market_brier": market_brier,
        "candidate_brier": candidate_brier,
        "delta_brier_vs_market": candidate_brier - market_brier,
        "market_log_loss": market_log_loss,
        "candidate_log_loss": candidate_log_loss,
        "delta_log_loss_vs_market": candidate_log_loss - market_log_loss,
    }


def fit_market_residual_stack(
    p_market: Iterable[float],
    p_senex: Iterable[float],
    labels: Iterable[int],
    *,
    l2: float = 1e-3,
    max_iter: int = 100,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    """Fit one interpretable SENEX residual beta around the market logit offset.

    Model:
        logit(p) = logit(p_market)
                   + beta_senex_residual
                     * (logit(p_senex) - logit(p_market))

    beta=0 is exactly market-only. This is historical/synthetic diagnostics,
    not a prospective challenger until separately preregistered.
    """
    market = [common.probability(value, name="p_market") for value in p_market]
    senex = [common.probability(value, name="p_senex") for value in p_senex]
    ys = [common.binary_label(value) for value in labels]
    if not ys or not (len(ys) == len(market) == len(senex)):
        raise EvaluationContractError("market, SENEX and labels must be aligned")
    if not math.isfinite(l2) or l2 < 0:
        raise EvaluationContractError("l2 must be finite and non-negative")

    offsets = [common.logit(p) for p in market]
    residuals = [
        common.logit(s) - common.logit(m)
        for m, s in zip(market, senex)
    ]
    beta = 0.0
    iterations = 0
    for iterations in range(1, max_iter + 1):
        predicted = [
            common.sigmoid(offset + beta * residual)
            for offset, residual in zip(offsets, residuals)
        ]
        gradient = sum(
            (p - y) * residual
            for p, y, residual in zip(predicted, ys, residuals)
        ) + l2 * beta
        hessian = sum(
            p * (1.0 - p) * residual * residual
            for p, residual in zip(predicted, residuals)
        ) + l2
        if hessian <= 1e-15:
            break
        updated = max(0.0, min(20.0, beta - gradient / hessian))
        if abs(updated - beta) <= tolerance:
            beta = updated
            break
        beta = updated

    probabilities = [
        common.sigmoid(offset + beta * residual)
        for offset, residual in zip(offsets, residuals)
    ]
    return {
        "model": "LOGIT_MARKET_OFFSET_PLUS_SENEX_RESIDUAL",
        "beta_senex_residual": beta,
        "iterations": iterations,
        "probabilities": probabilities,
        "comparison_vs_market": compare_to_market(ys, market, probabilities),
    }


def evaluate_historical_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate only historical/synthetic labeled rows.

    Prospective rows are rejected structurally so Challenger Lab development
    cannot become a side channel for early prospective score inspection.
    """
    if not records:
        raise EvaluationContractError("historical/synthetic records are required")

    labels: list[int] = []
    market: list[float] = []
    candidate: list[float] = []
    roles: set[str] = set()
    for row in records:
        role = str(row.get("dataset_role") or "").upper()
        if role == "PROSPECTIVE":
            raise EvaluationContractError("prospective rows are forbidden in historical runner")
        if role not in {"HISTORICAL", "SYNTHETIC"}:
            raise EvaluationContractError("dataset_role must be HISTORICAL or SYNTHETIC")
        roles.add(role)
        labels.append(common.binary_label(row.get("label")))
        market.append(common.probability(row.get("p_market"), name="p_market"))
        candidate.append(
            common.probability(
                row.get("candidate_probability"),
                name="candidate_probability",
            )
        )

    return {
        "phase": "HISTORICAL_SYNTHETIC_ONLY",
        "dataset_roles": sorted(roles),
        "n": len(records),
        **compare_to_market(labels, market, candidate),
    }
