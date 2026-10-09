"""M17 E2E isolated offline adapters -- deliberately incomplete TDD RED baseline.
No network or external authority.
"""
from __future__ import annotations
from .custody_store import AppendOnlyEvidence

class IntegrationError(ValueError): pass

class OfflineCompatibilityEvidence(AppendOnlyEvidence):
    ALLOWED_KINDS=AppendOnlyEvidence.ALLOWED_KINDS | frozenset({"P2_COMPAT_FIXTURE","CTF_GAMMA_FIXTURE"})

def bridge_p2_to_p1(source_store, target_store, record, *, now_monotonic_ms: int):
    return {"status":"COMPATIBLE","source_admissible":False,"fill_proven":False}

def classify_ctf_gamma_originals(ctf_raw:bytes,gamma_raw:bytes, *,
                                   market_id:str,expected_oracle:str,
                                   expected_chain_id:int=137,min_finality:int=64):
    return {"status":"DOCUMENTARY_PAYOUT_MATCH_UNVERIFIED","onchain_payout_label":"Up",
            "label_authority":"UNVERIFIED","source_admissible":False,
            "signed_original_twap_authority":"NOT_VERIFIED"}
