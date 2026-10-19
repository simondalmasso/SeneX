"""Explicit HARD schema gate: jsonschema Draft 2020-12 required; no fallback.

Run separately from isolated research/test-only environment:
PYTHONPATH=<temporary-jsonschema-install> python -m research.edge.oracle_aligned_net_ev.strict_schema_gate
"""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path


def run() -> dict:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        raise RuntimeError(
            "SCHEMA_GATE_BLOCKED: install pinned jsonschema==4.26.0 in a test-only directory"
        ) from exc
    root=Path(__file__).resolve().parents[3]
    # tests are not a production import: this command exists solely to verify
    # whether original positive / negative fixtures are valid under Draft 2020-12.
    sys.path.insert(0,str(root / "tests"))
    from test_oracle_net_p1 import make_receipts
    t0,t1,*_=make_receipts()
    lane=Path(__file__).resolve().parent
    validations, negatives=0,0
    for file,doc in (("T0_RECEIPT_SCHEMA_V1.json",t0),("T1_LABEL_SCHEMA_V1.json",t1)):
        schema=json.loads((lane/file).read_text(encoding="utf8"))
        Draft202012Validator.check_schema(schema)
        validator=Draft202012Validator(schema)
        validator.validate(doc)
        validations+=1
        tamper=copy.deepcopy(doc)
        tamper["schema_extra_key"]=1
        if not list(validator.iter_errors(tamper)):
            raise RuntimeError("SCHEMA_GATE_FAILED: extra key was accepted")
        negatives+=1
        tamper=copy.deepcopy(doc)
        tamper["start_ms"]=True
        if not list(validator.iter_errors(tamper)):
            raise RuntimeError("SCHEMA_GATE_FAILED: boolean start_ms was accepted")
        negatives+=1
        tamper=copy.deepcopy(doc)
        tamper.pop("market_id")
        if not list(validator.iter_errors(tamper)):
            raise RuntimeError("SCHEMA_GATE_FAILED: absent market_id accepted")
        negatives+=1
        tamper=copy.deepcopy(doc)
        tamper["rule_version_sha256"]="broken"
        if not list(validator.iter_errors(tamper)):
            raise RuntimeError("SCHEMA_GATE_FAILED: malformed sha256 accepted")
        negatives+=1
    return {"validator":"jsonschema.Draft202012Validator",
            "pinned_dependency":"jsonschema==4.26.0",
            "schema_positive_count":validations,
            "negative_mutations_rejected":negatives,
            "fallback":False,
            "status":"SCHEMA_HARD_GATE_PASS"}


if __name__=="__main__":
    try: print(json.dumps(run(),sort_keys=True))
    except Exception as exc:
        print(type(exc).__name__+": "+str(exc),file=sys.stderr)
        raise SystemExit(2)
