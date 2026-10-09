"""Machine-verifiable width/status safety of M17 E2E release artifacts."""
import csv,json
from pathlib import Path

R=Path(__file__).resolve().parents[1]/"research/edge/oracle_aligned_net_ev"

def test_e2e_evidence_csv_width_and_nonpromotion():
    with (R/"M17_E2E_EVIDENCE_MATRIX.csv").open(newline="",encoding="utf8") as f:
        rows=list(csv.reader(f))
    assert len(rows)==5
    head=rows[0]
    assert len(head)==12 and len(set(head))==12
    assert all(len(row)==len(head) for row in rows[1:])
    data=[dict(zip(head,row)) for row in rows[1:]]
    assert [x["window_denominator"] for x in data]==["1","1","1","0"]
    assert all(x["decision"]=="HOLD" for x in data)
    assert all(x["source_authority"]=="UNVERIFIED" for x in data)
    assert all("FILL_UNVERIFIED" in x["quote_fill_status"] for x in data)


def test_e2e_release_gate_not_overpromoted():
    j=json.loads((R/"M17_E2E_RELEASE_GATE.json").read_text(encoding="utf8"))
    assert j["no_global_pass"] is True
    assert j["zero_spend"]=="HARD"
    assert j["paper_only"] and not j["live"]
    assert j["real_orders"]==j["capital"]==j["n_real_verified_m17"]==0
    assert j["source_admissible"] is j["cohort_authorized"] is j["economic_edge_proven"] is False
    assert j["edge"]=="UNPROVEN" and j["no_merge"] and j["no_deploy"]
    allowed={"PASS_FIXTURE_ONLY","HOLD","NOT_IDENTIFIABLE","REQUIRES_SEPARATE_OWNER_AUTH"}
    assert all(z in allowed for z in j["gates"].values())
    for must in ("original_chain_tx_and_gamma_receipt","signed_chainlink_twap_authority","independent_aud_acceptance"):
        assert j["gates"][must]!="PASS_FIXTURE_ONLY"
