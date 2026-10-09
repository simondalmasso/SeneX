"""M17 offline fixture-only T0/T1 receipts with fail-closed eligibility.

No HTTP/WebSocket clients, no oracle authority, no orders. Fixture timestamps
cannot be promoted to genuine receipt timestamps.
"""
from __future__ import annotations
import json
from decimal import Decimal, InvalidOperation
from .custody_store import AppendOnlyEvidence, IntegrityError


class CaptureError(ValueError):
    """Invalid identity, clock, source or sample."""


def _ms(v: object, field: str):
    if type(v) is not int or v < 0:
        raise CaptureError("invalid "+field)
    return v


def _slot(slot: dict) -> str:
    if not isinstance(slot,dict):
        raise CaptureError("missing identity")
    for key in ("market_id","condition_id","market_slug","token_id_yes",
                "token_id_no","oracle_source_id"):
        if not isinstance(slot.get(key),str) or not slot[key]:
            raise CaptureError("missing "+key)
    if slot["token_id_yes"]==slot["token_id_no"]:
        raise CaptureError("same yes/no token ID")
    if slot["oracle_source_id"]!="btc-5m-twap-60":
        raise CaptureError("market-specific TWAP60 required")
    s,e =_ms(slot.get("start_ms"),"start_ms"),_ms(slot.get("end_ms"),"end_ms")
    if s%300000 or e-s!=300000:
        raise CaptureError("unaligned or non-5m window")
    return f'{slot["market_id"]}:{slot["condition_id"]}:{s}'


def _parse(raw: bytes):
    try:
        obj=json.loads(raw,parse_float=Decimal,
                       parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (ValueError,TypeError,UnicodeError,InvalidOperation) as exc:
        raise CaptureError("malformed supplied raw JSON bytes") from exc
    if not isinstance(obj,dict):
        raise CaptureError("raw JSON source not an object")
    return obj


def _decimal_str(v):
    try:
        if isinstance(v,bool):raise TypeError()
        num=Decimal(str(v))
    except (TypeError,InvalidOperation,ValueError) as exc:
        raise CaptureError("invalid exact Decimal field") from exc
    if not num.is_finite():raise CaptureError("nonfinite Decimal field")
    return format(num,"f")


class OfflineCapture:
    def __init__(self, store: AppendOnlyEvidence):
        if not isinstance(store,AppendOnlyEvidence):
            raise CaptureError("requires append-only offline ledger")
        self.store=store

    def record_t0(self, slot: dict, *, received_at_ms: int,
                  raw_sources: dict[str,bytes],source_clocks: dict,signal: dict | None,
                  max_age_ms: int=1500) -> dict:
        key=_slot(slot)
        now=_ms(received_at_ms,"received_at_ms")
        if now < slot["start_ms"] or now >= slot["end_ms"]:
            raise CaptureError("retrospective or out-of-window T0 forbidden")
        if type(max_age_ms) is not int or max_age_ms<=0:
            raise CaptureError("unfrozen freshness tolerance")
        if not isinstance(raw_sources,dict) or not isinstance(source_clocks,dict):
            raise CaptureError("missing original source lists")
        if set(raw_sources).difference(AppendOnlyEvidence.ALLOWED_ATTACHMENTS):
            raise CaptureError("unknown original source attachment")
        flags=set()
        if not raw_sources.get("market_rule") or not raw_sources.get("market_metadata"):
            flags.add("NO_MARKET_RULE")
        for key_name in ("book_yes","book_no"):
            raw=raw_sources.get(key_name)
            if not raw:
                flags.add("NO_BOOK")
                continue
            doc=_parse(raw)
            token_id=slot["token_id_yes" if key_name=="book_yes" else "token_id_no"]
            if str(doc.get("asset_id"))!=token_id or (
                not isinstance(doc.get("asks"),list) or not isinstance(doc.get("bids"),list)):
                flags.add("NO_BOOK")
            clock=source_clocks.get(key_name)
            if not isinstance(clock,dict):
                flags.add("STALE");continue
            ts=_ms(clock.get("event_ms"),"book event ms")
            recv=_ms(clock.get("received_ms"),"book local received ms")
            if ts>recv or recv>now:raise CaptureError("future book timestamp")
            # Evidence cannot be admitted if the source timestamp and decoded wire timestamp differ.
            try: book_ts=int(doc.get("timestamp"))
            except (TypeError,ValueError) as exc: raise CaptureError("missing wire book timestamp") from exc
            if book_ts!=ts:raise CaptureError("book timestamp mismatch")
            if now-ts>max_age_ms:flags.add("STALE")
            dropped=clock.get("dropped",0)
            seq=clock.get("seq")
            if type(dropped) is not int or dropped<0:
                raise CaptureError("invalid dropped count")
            prev=clock.get("previous_seq")
            if (type(seq) is not int or type(prev) is not int or
                seq != prev + 1 or dropped > 0):
                flags.add("GAP")
        for key_name in ("fee_yes","fee_no"):
            raw=raw_sources.get(key_name)
            if not raw:
                flags.add("NO_FEE");continue
            clock=source_clocks.get(key_name)
            if not isinstance(clock,dict):
                flags.add("NO_FEE");continue
            ts=_ms(clock.get("event_ms"),"fee event time")
            recv=_ms(clock.get("received_ms"),"fee receive time")
            if ts>recv or recv>now:
                raise CaptureError("future fee timestamp")
            if now-ts>max_age_ms:flags.add("STALE")
            fee=_parse(raw)
            token=slot["token_id_yes" if key_name=="fee_yes" else "token_id_no"]
            sc=fee.get("feeSchedule")
            if str(fee.get("token_id"))!=token or not isinstance(sc,dict):
                flags.add("NO_FEE");continue
            try:
                rate=Decimal(_decimal_str(sc.get("rate")))
                exponent=sc.get("exponent")
                if rate<0 or rate>1 or type(exponent) is not int or exponent!=1:
                    flags.add("NO_FEE")
            except CaptureError:
                flags.add("NO_FEE")
        raw_sig=raw_sources.get("senex_signal")
        if signal is None or not isinstance(signal,dict) or not raw_sig:
            flags.add("NO_SIGNAL")
        else:
            sig_doc=_parse(raw_sig)
            if (not isinstance(signal.get("prediction_id"),str) or
                not signal["prediction_id"] or signal.get("horizon_s")!=300 or
                signal.get("market_id")!=slot["market_id"] or
                sig_doc.get("prediction_id")!=signal["prediction_id"] or
                sig_doc.get("horizon_s")!=300 or
                sig_doc.get("market_id")!=slot["market_id"] or
                type(sig_doc.get("produced_at_ms")) is not int or
                sig_doc["produced_at_ms"]!=signal.get("produced_at_ms") or
                sig_doc.get("score_decimal") is None or
                str(sig_doc.get("score_decimal")) != str(signal.get("score_decimal")) or
                _ms(signal.get("produced_at_ms"),"signal time")>now):
                flags.add("NO_SIGNAL")
            else:
                try:
                    prob=Decimal(_decimal_str(signal.get("score_decimal")))
                    if prob<0 or prob>1:flags.add("NO_SIGNAL")
                except CaptureError:flags.add("NO_SIGNAL")
        if flags:flags.add("ABSTAIN")
        # No path in this *fixture* executable returns SOURCE_ADMISSIBLE.
        attrs={"slot":dict(slot),"received_at_ms":now,
               "source_clocks":source_clocks,"flags":sorted(flags),
               "eligible":False,"fixture_inputs_complete":not flags,
               "window_denominator":1,
               "strategy_pnl_decimal":"0",
               "source_admissible":False,
               "source_class":"FIXTURE_PROVENANCE_ONLY",
               "signal_horizon_verified_5m":bool(
                   signal and "NO_SIGNAL" not in flags and raw_sig),
               "precision_contract":"raw bytes; parse_float=Decimal; never authoritative"}
        return self.store.append(kind="T0_SLOT",slot_key=key,now_ms=now,
                                 artifacts=raw_sources,attrs=attrs)

    def record_missing_window(self,slot: dict,*,detected_at_ms:int) -> dict:
        key=_slot(slot);now=_ms(detected_at_ms,"detection time")
        # This is NOT an original T0; a past lost window cannot be reconstructed.
        if now < slot["end_ms"]:
            raise CaptureError("cannot mark a future window as lost")
        return self.store.append(kind="MISSED_WINDOW",slot_key=key,now_ms=now,
              artifacts={},attrs={"slot":dict(slot),"window_denominator":1,
                                   "flags":["MISSED_WINDOW","ABSTAIN"],
                                   "eligible":False,"strategy_pnl_decimal":"0",
                                   "source_admissible":False,
                                   "no_retrospective_t0":True})

    def record_t1(self,slot:dict,*,received_at_ms:int,raw_bytes:bytes,
                  source_class:str) -> dict:
        key=_slot(slot);now=_ms(received_at_ms,"T1 received time")
        if now < slot["end_ms"]:
            raise CaptureError("pre-terminal T1 forbidden")
        if source_class not in (
            "PLATFORM_TERMINAL_RESOLUTION","PROVIDER_CHAINLINK_RELAY",
            "CHAINLINK_SIGNED_REPORT"
        ):
            raise CaptureError("unknown source class")
        if not raw_bytes:raise CaptureError("T1 original source bytes required")
        return self.store.append(kind="T1_OBSERVATION",slot_key=key,now_ms=now,
               artifacts={"t1_original":raw_bytes},
               attrs={"source_class":source_class,
                      "label_authority":"UNVERIFIED","source_admissible":False,
                      "n_real_verified":0,"settlement_independent":False,
                      "status":"FIXTURE_ONLY_T1_NO_SCIENTIFIC_JOIN"})

    def record_transport_event(self,*,kind:str,received_at_ms:int,
                               connection_id:str,raw_frame:bytes,
                               previous_seq:int|None=None,
                               current_seq:int|None=None) -> dict:
        if kind not in ("RECONNECT","GAP"):
            raise CaptureError("invalid transport event")
        if not isinstance(connection_id,str) or not connection_id:
            raise CaptureError("missing connection identifier")
        gap=kind=="GAP" or (previous_seq is not None and (
            current_seq is None or current_seq!=previous_seq+1))
        return self.store.append(kind=kind,slot_key="transport:"+connection_id,
          now_ms=_ms(received_at_ms,"transport receive time"),
          artifacts={"raw_frame":raw_frame},
          attrs={"connection_id":connection_id,
                 "previous_seq":previous_seq,"current_seq":current_seq,
                 "has_gap":gap,"source_admissible":False,
                 "no_t0_reconstruction":True})
