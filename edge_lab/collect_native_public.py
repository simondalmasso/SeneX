from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import httpx

from edge_lab.native_cohort import FreezeManifest, NativeCohortCollector
from senecio_polymarket.backend.settlement_proof import is_proof_qualified


def _manifest(path: str | Path) -> FreezeManifest:
    payload=json.loads(Path(path).read_text(encoding="utf-8"))
    return FreezeManifest(
        cohort_id=payload["cohort_id"],
        registered_at_utc=payload["registered_at_utc"],
        code_hash=payload["code_hash"],
        config_hash=payload["config_hash"],
        effective_weights_hash=payload["effective_weights_hash"],
        feature_availability_policy=payload["feature_availability_policy"],
        exchange_policy=payload["exchange_policy"],
        exchange_used=payload["exchange_used"],
        symbol=payload.get("symbol","BTCUSDT"),
        horizon_seconds=int(payload.get("horizon_seconds",3600)),
    )


def collect_public_once(
    *,
    base_url: str,
    manifest_path: str | Path,
    cohort_path: str | Path,
    limit: int = 50,
) -> dict[str, Any]:
    manifest=_manifest(manifest_path)
    url=base_url.rstrip("/")+"/api/oracle/predictions/db"
    with httpx.Client(timeout=httpx.Timeout(20.0,connect=5.0),follow_redirects=False) as client:
        response=client.get(url,params={"limit":max(1,min(int(limit),50)),"symbol":manifest.symbol})
        response.raise_for_status()
        payload=response.json()
    rows=payload.get("predictions") if isinstance(payload,dict) else None
    if not isinstance(rows,list):
        raise ValueError("unexpected SENEX public predictions payload")
    proof=[row for row in rows if isinstance(row,dict) and is_proof_qualified(row)]
    collector=NativeCohortCollector(manifest,cohort_path)
    records=collector.collect_many(proof)
    return {
        "source":"SENEX_PUBLIC_READ_ONLY",
        "url":url,
        "rows_seen":len(rows),
        "proof_qualified_seen":len(proof),
        "records_considered":len(records),
        "authority_candidates_total":sum(
            row.get("cohort_status")=="AUTHORITY_CANDIDATE"
            for row in collector._existing()
        ),
        "edge_status":"UNPROVEN",
        "brier_logloss_allowed":False,
    }


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--base-url",required=True)
    parser.add_argument("--manifest",default="research/hyp001c_freeze_manifest.json")
    parser.add_argument("--cohort",default="research/hyp001c_native_cohort.jsonl")
    parser.add_argument("--limit",type=int,default=50)
    args=parser.parse_args()
    print(json.dumps(collect_public_once(
        base_url=args.base_url,
        manifest_path=args.manifest,
        cohort_path=args.cohort,
        limit=args.limit,
    ),sort_keys=True))


if __name__=="__main__":
    main()
