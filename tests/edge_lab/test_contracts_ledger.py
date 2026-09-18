import json

from edge_lab.contracts import ExperimentRecord, canonical_hash
from edge_lab.experiment_ledger import ExperimentLedger
from edge_lab.replay import canonicalize_hypothesis


def _record(hypothesis_id="HYP-001", verdict="INCONCLUSIVE"):
    return ExperimentRecord(
        hypothesis_id=hypothesis_id,
        timestamp="2026-09-17T23:00:00Z",
        code_hash="code123",
        config_hash="cfg123",
        source_data=["SENEX persisted audit", "Polymarket decision-time snapshot"],
        source_timestamp="2026-09-17T22:00:00Z",
        data_freshness="DECISION_TIME",
        market="BTCUSDT / Polymarket BTC Up-Down",
        condition_id="0xabc",
        token_id="123",
        horizon="5m-vs-1h",
        regime="UNCLASSIFIED",
        baseline_definition="Polymarket p_market",
        candidate_definition="SENEX p_senex",
        in_sample_window=None,
        oos_window=None,
        prospective=False,
        lookahead_check="PASS_CONTRACT_ONLY",
        leakage_check="PASS_CONTRACT_ONLY",
        n=0,
        resolved_n=0,
        abstentions=0,
        p_market=None,
        p_senex=None,
        brier=None,
        logloss=None,
        ece=None,
        long_wr=None,
        short_wr=None,
        spread=None,
        fees=None,
        slippage=None,
        latency=None,
        gross_ev=None,
        net_ev=None,
        pnl=None,
        profit_factor=None,
        max_drawdown=None,
        uncertainty_interval=None,
        time_window_stability=None,
        regime_stability=None,
        verdict=verdict,
        failure_reason="HORIZON_MISMATCH",
        notes="feasibility gate only",
    )


def test_canonical_hash_is_order_stable():
    assert canonical_hash({"b": 2, "a": 1}) == canonical_hash({"a": 1, "b": 2})


def test_ledger_is_append_only_and_detects_exact_duplicate(tmp_path):
    ledger = ExperimentLedger(tmp_path / "experiments.jsonl")
    first = ledger.append(_record())
    duplicate = ledger.find_duplicate(_record())
    assert duplicate is not None
    assert duplicate["record_hash"] == first["record_hash"]
    rows = [json.loads(line) for line in (tmp_path / "experiments.jsonl").read_text().splitlines()]
    assert len(rows) == 1


def test_negative_result_memory_finds_semantic_duplicate(tmp_path):
    ledger = ExperimentLedger(tmp_path / "experiments.jsonl")
    ledger.append(_record(hypothesis_id="HYP-005", verdict="REJECT"))
    hit = ledger.find_semantic_duplicate(
        hypothesis_id="HYP-005",
        candidate_definition="  SENEX   p_senex ",
        baseline_definition="Polymarket p_market",
        horizon="5m-vs-1h",
        regime="UNCLASSIFIED",
    )
    assert hit is not None
    assert hit["verdict"] == "REJECT"


def test_hypothesis_canonicalization_is_whitespace_and_case_stable():
    assert canonicalize_hypothesis("  Order-Book   IMBALANCE ") == "order-book imbalance"
