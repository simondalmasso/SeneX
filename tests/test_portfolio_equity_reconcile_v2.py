import pytest
from senecio_polymarket.backend.portfolio.execution_engine import ExecutionEngine, Position
from senecio_polymarket.backend.portfolio.portfolio_engine import PortfolioEngine


def _state_for(direction: str, last_prices: dict[str,float]):
    e = ExecutionEngine(config={"starting_cash": 10000.0})
    p = Position(position_id="p1", symbol="BTCUSDT", direction=direction, qty=1.0,
        avg_entry_price=100.0, entry_ts="2026-09-12T00:00:00+00:00",
        stop_price=95.0, target_price=105.0)
    e.positions["BTCUSDT"] = p
    e.cash = 9900.0 if direction == "LONG" else 10100.0
    state = PortfolioEngine().recompute_state(open_positions={"BTCUSDT": p.to_dict()},
        cash=e.cash, starting_equity=10000.0, last_prices=last_prices)
    return e, state

@pytest.mark.parametrize("direction,last", [("LONG",110.0),("SHORT",90.0)])
def test_portfolio_equity_matches_engine_with_fresh_price(direction,last):
    e,state=_state_for(direction,{"BTCUSDT":last})
    assert state.equity == pytest.approx(e.equity({"BTCUSDT":last}), abs=0.01)

@pytest.mark.parametrize("direction", ["LONG","SHORT"])
def test_portfolio_equity_uses_avg_entry_when_price_missing(direction):
    e,state=_state_for(direction,{})
    assert state.equity == pytest.approx(e.equity({}), abs=0.01)
