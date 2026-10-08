import math
import pytest
from research.edge.candle_economic_challenger.economic_labels import CostModel, economic_trade
from research.edge.candle_economic_challenger.contracts import EvidenceError

def test_long_short_and_flat_return_signed_net_bps():
    c=CostModel(fee_bps=10,spread_bps=0,slippage_bps=5,latency_bps=0,funding_bps=0)
    buy=economic_trade("LONG",100,101,c,horizon_seconds=3600)
    sell=economic_trade("SHORT",100,101,c,horizon_seconds=3600)
    flat=economic_trade("FLAT",100,101,c,horizon_seconds=3600)
    assert buy["gross_bps"]==pytest.approx(100)
    assert buy["net_bps"]==pytest.approx(85)
    assert sell["net_bps"]==pytest.approx(-115)
    assert flat["net_bps"]==0 and flat["cost_bps"]==0 and flat["pnl_usdt"]==0
    assert buy["pnl_usdt"]==pytest.approx(8.5)

def test_cost_components_are_once_only_and_exact_one_hour():
    c=CostModel(10,1,2,1,1)
    got=economic_trade("LONG",100,100,c,horizon_seconds=3600,notional_usdt=1000)
    assert got["net_bps"]==-15
    assert got["cost_breakdown_bps"]=={"fee":10,"spread":1,"slippage":2,"latency":1,"funding":1}
    with pytest.raises(EvidenceError,match="horizon"): economic_trade("LONG",100,100,c,horizon_seconds=3599)

@pytest.mark.parametrize("bad",[float("nan"),float("inf"),-1,0])
def test_invalid_reference_is_ineligible(bad):
    with pytest.raises(EvidenceError):economic_trade("LONG",bad,100,CostModel(10,0,5,0,0),horizon_seconds=3600)
