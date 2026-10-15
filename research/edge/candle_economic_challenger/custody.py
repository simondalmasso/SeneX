"""Strict raw-byte identity binding; no caller-controlled custody bool suffices."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
from .contracts import EvidenceError, digest, parse_snapshot, utc
from .candle_features import extract_features, classify_patterns

_ANCHORS=Path(__file__).with_name("CUSTODY_TRUST_ANCHORS_V1.json")

@dataclass(frozen=True)
class VerifiedCustodyAttestation:
    rows_sha256: str
    trusted_anchor_sha256: str
    source_label_bindings: tuple[tuple[str,str,str], ...]
    contract: str = "senex-candle-exact-byte-attestation-v1"

def _sha(raw):
    return hashlib.sha256(raw).hexdigest()

def _object(raw,kind):
    if type(raw) is not bytes:
        raise EvidenceError(f"{kind} must be original exact bytes")
    try: obj=json.loads(raw.decode("utf-8"))
    except (UnicodeError,ValueError) as exc: raise EvidenceError(f"invalid original {kind} bytes") from exc
    if not isinstance(obj,dict): raise EvidenceError(f"invalid {kind} object")
    return obj

def verify_exact_byte_attestation(rows,*,source_bytes,label_bytes):
    """Recompute row/feature/label bindings against repository-frozen AUD anchors.

    This code does NOT create pre-outcome provenance from a post-hoc hash.
    Empty or unapproved frozen registry yields None; no fabricated attestation.
    """
    if type(source_bytes) is not dict or type(label_bytes) is not dict:
        raise EvidenceError("original raw source and label bytes mappings required")
    raw_anchors=_ANCHORS.read_bytes()
    anchors=_object(raw_anchors,"trusted anchors")
    if anchors.get("status")!="AUD_VERIFIED_PRE_OUTCOME" or not anchors.get("entries"):
        return None
    approved=anchors["entries"]
    if len(rows)!=len(set(str(r.get("market_id")) for r in rows)):
        raise EvidenceError("duplicate source rows")
    bindings=[]
    for row in rows:
        key=str(row.get("market_id") or "")
        expected=approved.get(key)
        if not isinstance(expected,dict) or key not in source_bytes or key not in label_bytes:
            return None
        source=source_bytes[key]; label=label_bytes[key]
        if (_sha(source)!=expected.get("source_sha256") or
                _sha(label)!=expected.get("label_sha256")):
            raise EvidenceError("original bytes do not match frozen T0/label anchors")
        snap=_object(source,"T0 source"); outcome=_object(label,"1h label")
        parsed=parse_snapshot(snap)
        if (str(snap.get("prediction_id"))!=key or
                parsed["decision_ts"]!=utc(row["decision_ts"]).isoformat().replace("+00:00","Z") or
                snap.get("side")!=row.get("side") or
                str(outcome.get("prediction_id"))!=key):
            raise EvidenceError("T0-source-label decision identity mismatch")
        if any(outcome.get(field)!=snap.get(field) for field in ("venue","symbol","market_type")):
            raise EvidenceError("venue/symbol/market type mismatch")
        if (utc(outcome["decision_ts"])!=utc(row["decision_ts"]) or
                utc(outcome["label_end_ts"])!=utc(row["label_end_ts"]) or
                utc(outcome["label_end_ts"])-utc(row["decision_ts"])!=timedelta(hours=1)):
            raise EvidenceError("exact one-hour label lineage mismatch")
        # Entry reference must be present in original T0 bytes; an outcome
        # record cannot invent a convenient past fill or rewrite the T0 quote.
        try:
            original_entry=float(snap["price_now"])
        except (KeyError,TypeError,ValueError) as exc:
            raise EvidenceError("original T0 entry reference unavailable") from exc
        if not math.isfinite(original_entry) or original_entry<=0:
            raise EvidenceError("original T0 entry reference invalid")
        if not math.isclose(original_entry,float(outcome["entry_reference"]),rel_tol=0,abs_tol=1e-12):
            raise EvidenceError("entry reference mismatch against original T0 price")
        for field in ("entry_reference","exit_reference"):
            if not math.isclose(float(outcome[field]),float(row[field]),rel_tol=0,abs_tol=1e-12):
                raise EvidenceError("label price differs from original reference")
        observed=extract_features(parsed["hourly"])
        pattern=classify_patterns(parsed["hourly"])
        if any(not math.isclose(float(row["geometry"][f]),float(v),rel_tol=0,abs_tol=1e-12)
               for f,v in observed.items()) or pattern!=row["patterns"]:
            raise EvidenceError("features differ from exact original T0 bars")
        bindings.append((key,_sha(source),_sha(label)))
    if len(bindings)!=len(approved):
        return None
    return VerifiedCustodyAttestation(digest(rows),_sha(raw_anchors),tuple(sorted(bindings)))
