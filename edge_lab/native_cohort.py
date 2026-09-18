from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .contracts import canonical_hash


FEATURE_POLICY_VERSION = "missing-input-mask-v1"
EXCHANGE_POLICY = "EXACT_PERSISTED_EXCHANGE_NO_DEFAULT_NO_INFERENCE"
HORIZON_SECONDS = 3600


def _utc(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class FreezeManifest:
    cohort_id: str
    registered_at_utc: str
    code_hash: str
    config_hash: str
    effective_weights_hash: str
    feature_availability_policy: str
    exchange_policy: str
    exchange_used: str
    symbol: str = "BTCUSDT"
    horizon_seconds: int = HORIZON_SECONDS

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["manifest_hash"] = canonical_hash(payload)
        return payload


class NativeCohortCollector:
    """Append-only research collector over already-persisted SENEX rows.

    It never writes to SENEX storage. The default proof validator is the
    production settlement proof gate imported read-only.
    """

    def __init__(
        self,
        manifest: FreezeManifest,
        path: str | Path,
        *,
        proof_validator: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.manifest = manifest
        self.path = Path(path)
        if proof_validator is None:
            from senecio_polymarket.backend.settlement_proof import is_proof_qualified
            proof_validator = is_proof_qualified
        self._proof_validator = proof_validator

    def _existing(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows=[]
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def _last_selected_ts(self, status: str) -> datetime | None:
        candidates=[
            _utc(row["timestamp"])
            for row in self._existing()
            if row.get("cohort_status") == status
        ]
        return max(candidates) if candidates else None

    def _append(self, record: dict[str, Any]) -> dict[str, Any]:
        existing=self._existing()
        for row in existing:
            if row.get("source_prediction_id") == record.get("source_prediction_id"):
                return row
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        return record

    def collect(self, row: dict[str, Any]) -> dict[str, Any]:
        audit=row.get("audit") if isinstance(row.get("audit"), dict) else {}
        pipeline=audit.get("pipeline") if isinstance(audit.get("pipeline"), dict) else {}
        step2=pipeline.get("step2_features") if isinstance(pipeline.get("step2_features"), dict) else {}
        replay=audit.get("decision_replay_v1") if isinstance(audit.get("decision_replay_v1"), dict) else {}
        dual=audit.get("outcomes_dual") if isinstance(audit.get("outcomes_dual"), dict) else {}
        evidence_block=dual.get("price_evidence_v1") if isinstance(dual.get("price_evidence_v1"), dict) else {}
        evidence_1h=evidence_block.get("1h") if isinstance(evidence_block.get("1h"), dict) else None
        mask=step2.get("missing_input_mask_v1") if isinstance(step2.get("missing_input_mask_v1"), dict) else {}
        action_vector=audit.get("action_vector") if isinstance(audit.get("action_vector"), dict) else {}

        ts=str(row.get("ts") or "")
        timestamp=_utc(ts)
        registered=_utc(self.manifest.registered_at_utc)
        prospective=timestamp >= registered

        reasons: list[str]=[]
        proof_ok=bool(self._proof_validator(row))
        if not proof_ok:
            reasons.append("NOT_PROOF_QUALIFIED")

        actual_freeze={
            "code_hash": replay.get("code_hash"),
            "config_hash": replay.get("config_hash"),
            "effective_weights_hash": replay.get("effective_weights_hash"),
            "feature_availability_policy": mask.get("version"),
            "feature_missing_excluded_from_agreement_denominator": mask.get("missing_excluded_from_agreement_denominator"),
            "exchange_used": str(row.get("exchange_used") or "").lower(),
        }
        if prospective:
            if actual_freeze["code_hash"] != self.manifest.code_hash:
                reasons.append("CODE_HASH_MISMATCH")
            if actual_freeze["config_hash"] != self.manifest.config_hash:
                reasons.append("CONFIG_HASH_MISMATCH")
            if actual_freeze["effective_weights_hash"] != self.manifest.effective_weights_hash:
                reasons.append("EFFECTIVE_WEIGHTS_HASH_MISMATCH")
            if actual_freeze["feature_availability_policy"] != self.manifest.feature_availability_policy:
                reasons.append("FEATURE_AVAILABILITY_POLICY_MISMATCH")
            if actual_freeze["feature_missing_excluded_from_agreement_denominator"] is not True:
                reasons.append("FEATURE_AVAILABILITY_POLICY_MISMATCH")
            if str(row.get("exchange_used") or "").lower() != self.manifest.exchange_used.lower():
                reasons.append("EXCHANGE_POLICY_MISMATCH")

        raw_up_prob=step2.get("up_prob")
        try:
            raw_up_prob=float(raw_up_prob)
        except (TypeError, ValueError):
            raw_up_prob=None
            reasons.append("RAW_UP_PROB_MISSING")

        try:
            price_now=float(row.get("price_now"))
            price_1h=float(dual.get("price_1h_later"))
        except (TypeError, ValueError):
            price_now=None
            price_1h=None
            reasons.append("SETTLEMENT_PRICE_MISSING")

        if evidence_1h is None:
            reasons.append("SETTLEMENT_EVIDENCE_1H_MISSING")

        if not prospective:
            if reasons:
                status="EXCLUDED_INVALID"
            else:
                last=self._last_selected_ts("DIAGNOSTIC_ONLY_HISTORICAL")
                if last is not None and (timestamp-last).total_seconds() < self.manifest.horizon_seconds:
                    status="EXCLUDED_OVERLAP"
                    reasons.append("NONOVERLAP_1H_VIOLATION")
                else:
                    status="DIAGNOSTIC_ONLY_HISTORICAL"
        elif reasons:
            status="EXCLUDED_FREEZE_MISMATCH" if any(x.endswith("MISMATCH") for x in reasons) else "EXCLUDED_INVALID"
        else:
            last=self._last_selected_ts("AUTHORITY_CANDIDATE")
            if last is not None and (timestamp-last).total_seconds() < self.manifest.horizon_seconds:
                status="EXCLUDED_OVERLAP"
                reasons.append("NONOVERLAP_1H_VIOLATION")
            else:
                status="AUTHORITY_CANDIDATE"

        native_y_up=(1 if price_1h > price_now else 0) if price_now is not None and price_1h is not None else None
        record={
            "version":"hyp001c-native-cohort-record-v1",
            "cohort_id":self.manifest.cohort_id,
            "manifest_hash":self.manifest.to_dict()["manifest_hash"],
            "source_prediction_id":row.get("id"),
            "timestamp":ts,
            "symbol":row.get("symbol"),
            "action":action_vector.get("action"),
            "direction":step2.get("direction"),
            "final_prediction":row.get("prediction"),
            "exchange_used":row.get("exchange_used"),
            "price_now":price_now,
            "price_1h_later":price_1h,
            "raw_up_prob":raw_up_prob,
            "raw_up_prob_transformed":False,
            "raw_up_prob_semantics":"LOGISTIC_SQUASHED_ENGINEERED_PRESSURE_SCORE",
            "native_y_up":native_y_up,
            "native_directional_outcome":dual.get("outcome_1h") or row.get("outcome"),
            "settlement_evidence_1h":copy.deepcopy(evidence_1h),
            "settlement_observation_v1":copy.deepcopy(dual.get("settlement_observation_v1")),
            "freeze_observed":actual_freeze,
            "cohort_status":status,
            "authority_candidate":status=="AUTHORITY_CANDIDATE",
            "diagnostic_only":status=="DIAGNOSTIC_ONLY_HISTORICAL",
            "exclusion_reasons":list(dict.fromkeys(reasons)),
            "brier_logloss_allowed":False,
        }
        record["record_hash"]=canonical_hash(record)
        return self._append(record)

    def collect_many(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ordered=sorted(rows, key=lambda row: _utc(row.get("ts")))
        return [self.collect(row) for row in ordered]
