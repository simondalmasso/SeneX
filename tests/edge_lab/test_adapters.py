import pytest

from edge_lab.adapters.polymarket_v2 import PolymarketV2Adapter
from edge_lab.adapters.senex_readonly import extract_probability_observation


def test_polymarket_v2_adapter_accepts_only_documented_read_endpoints():
    adapter = PolymarketV2Adapter()
    assert adapter.url_for("/trades").endswith("/v2/trades")
    assert adapter.url_for("/prices-history").endswith("/v2/prices-history")
    with pytest.raises(ValueError):
        adapter.url_for("/orders")
    with pytest.raises(ValueError):
        adapter.url_for("https://evil.example/x")


def test_senex_readonly_extracts_decision_time_snapshot_and_marks_horizon_mismatch():
    row = {
        "outcome": "WIN",
        "prediction": "LONG",
        "audit": {
            "pipeline": {"step2_features": {"up_prob": 0.58}},
            "external_markets_v1": {
                "polymarket": {
                    "version": "polymarket-btc-5m-v1",
                    "up_probability": 0.62,
                    "condition_id": "0xabc",
                }
            },
        },
    }
    obs = extract_probability_observation(row, senex_horizon="1h")
    assert obs.p_market == 0.62
    assert obs.p_senex == 0.58
    assert obs.market_horizon == "5m"
    assert obs.senex_horizon == "1h"
    assert obs.p_senex_semantics == "UNVALIDATED_MODEL_UP_PROB"


def test_senex_readonly_rejects_missing_decision_time_market_prior():
    row = {
        "outcome": "WIN",
        "audit": {"pipeline": {"step2_features": {"up_prob": 0.58}}},
    }
    with pytest.raises(ValueError, match="decision-time"):
        extract_probability_observation(row)
