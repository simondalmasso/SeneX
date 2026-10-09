"""SENEX M17 E2E research compatibility, documentary-only synthetic labels.

Never fetches network data. This is NOT chain authentication or a source
authority gate; the "finality" field is a documentary fixture assertion.
The parent P1 custody code is imported unchanged on an isolated branch.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from .custody_store import AppendOnlyEvidence


class IntegrationError(ValueError):
    pass


class OfflineCompatibilityEvidence(AppendOnlyEvidence):
    """Append-only P1 validator with segregated fixture kinds, not T0/T1."""
    ALLOWED_KINDS = AppendOnlyEvidence.ALLOWED_KINDS | frozenset({
        "P2_COMPAT_FIXTURE", "CTF_GAMMA_FIXTURE"
    })


def _hex_id(value, chars):
    return isinstance(value, str) and bool(re.fullmatch(r"0x[0-9a-fA-F]{" + str(chars) + "}", value))


def _parse_original_json(raw):
    if type(raw) is not bytes or not raw or len(raw) > 100000:
        raise IntegrationError("bounded original JSON bytes required")
    def reject_duplicate(pairs):
        out={}
        for k,v in pairs:
            if k in out:
                raise IntegrationError("ambiguous duplicate original JSON key")
            out[k]=v
        return out
    try:
        doc=json.loads(raw,parse_float=Decimal,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)),
                       object_pairs_hook=reject_duplicate)
    except (ValueError,UnicodeError,TypeError) as exc:
        raise IntegrationError("malformed/ambiguous original fixture") from exc
    if type(doc) is not dict:
        raise IntegrationError("original fixture object required")
    return doc


def classify_ctf_gamma_originals(ctf_raw:bytes,gamma_raw:bytes, *,
                                  market_id:str,expected_oracle:str,
                                  expected_token_ids=None,expected_chain_id:int=137,
                                  min_finality:int=64) -> dict:
    """Inspect *supplied* documentary bytes. Positive != sourced label.

    Reject unauthenticated/ambiguous chain, outcome or Gamma mapping. An
    expected token map must come independently from the fixture's rule
    declaration, never inferred from the untrusted outcome order itself.
    """
    result={
      "status":"LABEL_UNVERIFIED",
      "source_admissible":False,"eligible":False,"fill_proven":False,
      "label_authority":"UNVERIFIED","onchain_terminal_label_status":"UNVERIFIED",
      "signed_original_twap_authority":"NOT_VERIFIED",
      "causal_T0_status":"NOT_EVALUATED","fee_version_provenance":"NOT_EVALUATED",
      "quote_vs_fill_evidence":"FILL_UNVERIFIED",
      "OOS_trial_registry":"NOT_AUTHORIZED",
      "fixture_class":"SYNTHETIC_DOCUMENTARY_NOT_CHAIN_RECEIPT",
      "ctf_original_sha256":hashlib.sha256(ctf_raw).hexdigest() if type(ctf_raw) is bytes else None,
      "gamma_original_sha256":hashlib.sha256(gamma_raw).hexdigest() if type(gamma_raw) is bytes else None,
      "onchain_payout_label":None,
    }
    try:
        c,g=_parse_original_json(ctf_raw),_parse_original_json(gamma_raw)
        if not (_hex_id(expected_oracle,40) and
                type(expected_chain_id) is int and expected_chain_id == 137 and
                type(min_finality) is int and min_finality >= 1 and
                isinstance(market_id,str) and market_id and
                isinstance(expected_token_ids,(list,tuple)) and
                len(expected_token_ids)==2 and
                all(isinstance(t,str) and bool(t) for t in expected_token_ids) and
                expected_token_ids[0]!=expected_token_ids[1]):
            raise IntegrationError("missing independent fixture identity contract")
        # CTF ConditionResolution is documentary v1 only. V2 PositionManager
        # requires independent positionId/contract provenance, never CTF fallthrough.
        if g.get("version") != "v1":
            raise IntegrationError("unsupported/unknown Gamma protocol version for CTF")
        if (type(c.get("chainId")) is not int or c["chainId"]!=expected_chain_id or
            type(g.get("chainId")) is not int or g["chainId"]!=expected_chain_id):
            raise IntegrationError("wrong/unverified chain")
        if not _hex_id(c.get("contract"),40) or (
              c["contract"].lower()!="0x4d97dcd97ec945f40cf65f87097ace5ea0476045"):
            raise IntegrationError("wrong CTF contract")
        if not _hex_id(c.get("oracle"),40) or c["oracle"].lower()!=expected_oracle.lower():
            raise IntegrationError("unknown resolution oracle")
        cond=c.get("conditionId")
        if not (_hex_id(cond,64) and g.get("conditionId")==cond and
                g.get("id")==market_id and _hex_id(c.get("questionId"),64)):
            raise IntegrationError("unbound market/condition/question")
        if (g.get("outcomes")!=["Up","Down"] or
            g.get("clobTokenIds")!=list(expected_token_ids)):
            raise IntegrationError("reversed/ambiguous token and outcome slots")
        pay=c.get("payoutNumerators")
        if (type(c.get("outcomeSlotCount")) is not int or c["outcomeSlotCount"]!=2 or
            type(pay) is not list or len(pay)!=2 or
            any(type(z) is not int for z in pay) or pay not in ([1,0],[0,1])):
            raise IntegrationError("nonbinary/ambiguous payout")
        winner="Up" if pay==[1,0] else "Down"
        if g.get("winningOutcome")!=winner:
            raise IntegrationError("Gamma winner disagrees with indexed payout")
        if not (type(c.get("blockNumber")) is int and c["blockNumber"]>0 and
                _hex_id(c.get("blockHash"),64) and
                _hex_id(c.get("transactionHash"),64) and
                type(c.get("logIndex")) is int and c["logIndex"]>=0 and
                c.get("removed") is False and
                type(c.get("finalityConfirmations")) is int and
                c["finalityConfirmations"]>=min_finality):
            raise IntegrationError("missing/reorged/unfinalized documentary log")
    except (IntegrationError,KeyError,TypeError,ValueError):
        return result
    result.update({
       "status":"DOCUMENTARY_PAYOUT_MATCH_UNVERIFIED",
       "onchain_payout_label":winner,
       "onchain_terminal_label_status":"SYNTHETIC_CONSISTENT_NOT_ATTESTED",
    })
    return result


def bridge_p2_to_p1(source_store, target_store:OfflineCompatibilityEvidence,
                    record:dict, *,now_monotonic_ms:int) -> dict:
    """Explicit segregated raw-frame archive, NOT an integrated T0 signal.

    P1/P2 journal formats are independent; never concatenate their chains.
    """
    if type(now_monotonic_ms) is not int or now_monotonic_ms<0:
        raise IntegrationError("invalid local monotonic ms")
    if not isinstance(target_store,OfflineCompatibilityEvidence) or type(record) is not dict:
        raise IntegrationError("not isolated compatibility custody")
    try:
        verified=source_store.reopen()
        if not any(z==record for z in verified):
            raise IntegrationError("not the exact verified P2 journal record")
        for flag in ("source_admissible","eligible","fill_proven"):
            if record.get(flag) is not False:
                raise IntegrationError("P2 source claim cannot be self-promoted")
        if record.get("label_authority")!="UNVERIFIED":
            raise IntegrationError("P2 label claim cannot be self-promoted")
        for field in ("market_id","condition_id","asset_id","connection_id",
                      "parser_version","source_class","original_sha256"):
            if not isinstance(record.get(field),str) or not record[field]:
                raise IntegrationError("missing input source identity")
        if (type(record.get("received_wall_ms")) is not int or
            type(record.get("received_monotonic_ns")) is not int or
            record["received_monotonic_ns"]<0 or record["received_wall_ms"]<0):
            raise IntegrationError("invalid distinct receipt clock")
        event=record.get("source_event_wall_ms")
        if event is not None and (
               type(event) is not int or event<0 or event>record["received_wall_ms"]):
            raise IntegrationError("future/noncanonical source event")
        raw=(source_store.path/record["original_attachment"]).read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest !=record["original_sha256"] or len(raw)!=record["original_length"]:
            raise IntegrationError("source byte custody digest mismatch")
        previous=target_store.verify()
        if any(z.get("p2_original_sha256")==digest for z in previous):
            raise IntegrationError("duplicate original provider bytes")
        attrs={
            "source_admissible":False,"eligible":False,"fill_proven":False,
            "label_authority":"UNVERIFIED","fixture_class":"CROSS_PR_P2_ORIGINAL_BYTES_ONLY",
            "p2_original_sha256":digest,"p2_record_chain_sha256":record["chain_sha256"],
            "p2_source_class":record["source_class"],"p2_parser_version":record["parser_version"],
            "p2_source_event_wall_ms":event,
            "p2_received_wall_ms":record["received_wall_ms"],
            "p2_received_monotonic_ns":record["received_monotonic_ns"],
            "market_id":record["market_id"],"condition_id":record["condition_id"],
            "asset_id":record["asset_id"],
            "compatibility_status":"RAW_BYTES_ONLY_NO_T0_OR_AUTHORITY",
        }
        key="p2:"+record["market_id"]+":"+record["condition_id"]+":"+digest[:16]
        return target_store.append(kind="P2_COMPAT_FIXTURE",slot_key=key,
                                   now_ms=now_monotonic_ms,
                                   artifacts={"raw_frame":raw},attrs=attrs)
    except IntegrationError:
        raise
    except (OSError,KeyError,TypeError,ValueError) as exc:
        raise IntegrationError("P2 raw bridge contract failed closed") from exc
