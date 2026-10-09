"""M17 offline TWAP60 frame validation: no network, signer or authentication.

Transport capabilities are NOT verified by this parser. Only live event 'update'
frames are accepted; snapshots and generic crypto_prices_chainlink spot are
disallowed. Never upgrade provider relay to signed Chainlink oracle evidence.
"""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, InvalidOperation


class SourceError(ValueError):
    """Wrong market, topic, time, gap, encoding or provenance."""


def classify_twap_transport(transport: str) -> dict:
    if transport=="SECURE_REALTIME":
        return {"requires_auth":True,"public_zero_spend_unverified":True,
                "independent_chainlink_attestation":False,
                "topic":"prices.crypto.twap","window_seconds":60,
                "status":"AUTH_REQUIRED_DO_NOT_FETCH_UNDER_ZERO_SPEND"}
    if transport=="LEGACY_RTDS":
        return {"requires_auth":False,"public_zero_spend_unverified":True,
                "independent_chainlink_attestation":False,
                "topic":"crypto_prices_twap_sixty","window_seconds":60,
                "status":"LEGACY_REACHABILITY_NOT_PROVEN"}
    raise SourceError("unrecognized TWAP transport; generic chainlink is NOT TWAP60")


def _strict_ms(value: object) -> int:
    if type(value) is not int or value < 0:
        raise SourceError("missing/noninteger source event timestamp")
    return value


def parse_twap_frame(
    raw: bytes, *, received_at_ms: int, transport: str,
    max_age_ms: int, previous_seq: int | None=None,
) -> dict:
    if not isinstance(raw, bytes) or not raw or len(raw)>65536:
        raise SourceError("no original bounded bytes")
    policy=classify_twap_transport(transport)
    acquired=_strict_ms(received_at_ms)
    if type(max_age_ms) is not int or max_age_ms <= 0:
        raise SourceError("unfrozen max-age")
    try:
        msg=json.loads(raw)
    except (ValueError,UnicodeDecodeError,TypeError) as exc:
        raise SourceError("not JSON raw bytes") from exc
    if not isinstance(msg,dict) or msg.get("topic")!=policy["topic"] or (
        msg.get("type")!="update"
    ):
        raise SourceError("wrong topic/type, snapshot, or generic spot")
    payload=msg.get("payload")
    if not isinstance(payload,dict):
        raise SourceError("missing payload")
    seq=msg.get("seq")
    if seq is not None and (type(seq) is not int or seq < 0):
        raise SourceError("invalid sequence")
    if previous_seq is not None and (
        type(previous_seq) is not int or seq is None or seq <= previous_seq
    ):
        raise SourceError("sequence went backward/missing")
    dropped=msg.get("dropped",0)
    if type(dropped) is not int or dropped < 0:
        raise SourceError("invalid dropped count")
    if transport=="SECURE_REALTIME":
        if payload.get("symbol")!="btcusd" or payload.get("windowSeconds")!=60:
            raise SourceError("modern feed requires btcusd and 60s TWAP")
        envelope_time=_strict_ms(msg.get("timestamp"))
        if envelope_time > acquired:
            raise SourceError("envelope timestamp from future")
        price_raw=payload.get("value")
        if not isinstance(price_raw,str):
            raise SourceError("modern price must be original Decimal string")
        try:
            value=Decimal(price_raw)
        except InvalidOperation as exc:
            raise SourceError("invalid Decimal TWAP") from exc
    else:
        if payload.get("symbol")!="btc/usd" or payload.get("window_s")!=60:
            raise SourceError("legacy symbol/window_s=60 mismatch")
        price_raw=payload.get("full_accuracy_value")
        if not isinstance(price_raw,str) or not price_raw.isdigit():
            raise SourceError("legacy TWAP requires exact E18 integer-string")
        value=Decimal(price_raw)/Decimal(10)**18
    event_time=_strict_ms(payload.get("timestamp"))
    if event_time > acquired or acquired-event_time > max_age_ms:
        raise SourceError("future or stale TWAP source event")
    if not value.is_finite() or value <= 0:
        raise SourceError("nonfinite/nonpositive TWAP")
    return {
        "transport":transport,"topic":policy["topic"],
        "source_class":"PROVIDER_CHAINLINK_RELAY",
        "independent_chainlink_attestation":False,
        "value_decimal":format(value,"f"),
        "source_timestamp_ms":event_time,
        "received_at_ms":acquired,"age_ms":acquired-event_time,
        "seq":seq,
        "gap_detected":seq is None or bool(dropped) or (
            previous_seq is not None and seq != previous_seq+1
        ),
        "raw_bytes_sha256":hashlib.sha256(raw).hexdigest(),
        "raw_byte_length":len(raw),
        "original_bytes_retention_required":True,
    }
