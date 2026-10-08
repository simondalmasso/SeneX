"""M17 standalone PAPER-only CLOB quote economics; NO trades or real fill claims."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation


class CostError(ValueError):
    """Unknown costs, inconsistent market, or invalid executable quote."""


def _d(value: object) -> Decimal:
    try:
        if isinstance(value, bool):
            raise ValueError("boolean is not a price/quantity")
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CostError("invalid numeric input") from exc
    if not number.is_finite():
        raise CostError("nonfinite numeric input")
    return number


def _rate(rate: object, exponent: object) -> Decimal:
    if rate is None or exponent != 1:
        raise CostError("UNVERIFIED_FEE: specific rate and audited exponent=1 required")
    r = _d(rate)
    if r < 0 or r > 1:
        raise CostError("invalid fee curve rate")
    return r


def fee_usdc(shares: object, price: object, *, fee_rate: object, exponent: int) -> float:
    """For an *explicitly verified* market whose fee formula has exponent=1.

    The example category rate 0.07 is never silently applied as a default.
    """
    r = _rate(fee_rate, exponent)
    q, p = _d(shares), _d(price)
    if q < 0 or p <= 0 or p >= 1:
        raise CostError("invalid fee quantity/price")
    return float(q * r * p * (1-p))


def walk_asks(levels: list, desired_shares: object) -> dict:
    """Deterministic quote-depth upper quantity bound, not an actual fill."""
    needed = _d(desired_shares)
    if needed <= 0:
        raise CostError("desired_shares must be positive")
    if not isinstance(levels, list):
        raise CostError("missing depth")
    parsed = []
    for level in levels:
        if not isinstance(level, dict) or "price" not in level or "size" not in level:
            raise CostError("invalid depth level")
        p, q = _d(level["price"]), _d(level["size"])
        if p <= 0 or p >= 1 or q <= 0:
            raise CostError("invalid ask price/size")
        parsed.append((p, q))
    parsed.sort(key=lambda item: item[0])
    filled, spent, fills = Decimal(0), Decimal(0), []
    for p, available in parsed:
        take = min(available, needed-filled)
        if take <= 0:
            break
        filled += take
        spent += p*take
        fills.append({"price": float(p), "shares": float(take)})
        if filled == needed:
            break
    return {
        "filled_shares": float(filled),
        "gross_cost": float(spent),
        "vwap_ask": float(spent/filled) if filled else None,
        "full_depth": filled == needed,
        "levels_taken": fills,
        "evidence": "PAPER_QUOTE_ONLY",
    }


def _level_fee(levels_taken: list, rate: object, exponent: int) -> float:
    return sum(fee_usdc(l["shares"], l["price"], fee_rate=rate, exponent=exponent) for l in levels_taken)


def hold_quote_net(
    levels: list, shares: object, *, outcome: int, fee_rate: object,
    exponent: int, slippage_usdc: object = 0
) -> dict:
    """Ex-post hypothetical quote payoff, NOT realized or OOS net alpha."""
    _rate(fee_rate, exponent)
    if outcome not in (0, 1):
        raise CostError("terminal outcome must be 0 or 1")
    slip = _d(slippage_usdc)
    if slip < 0:
        raise CostError("invalid slippage")
    quote = walk_asks(levels, shares)
    if quote["filled_shares"] == 0:
        return {**quote, "paper_net_usdc": 0.0, "evidence": "PAPER_QUOTE_ONLY"}
    fee = _level_fee(quote["levels_taken"], fee_rate, exponent)
    net = quote["filled_shares"]*outcome-quote["gross_cost"]-fee-float(slip)
    return {**quote, "fee_usdc": fee, "paper_net_usdc": net}


def paired_quote_upper_bound(
    up: list, down: list, shares: object, *, up_condition: str, down_condition: str,
    up_fee_rate: object, down_fee_rate: object, exponent: int, other_costs: object = 0
) -> dict:
    """A complete-set quote is NOT atomic; only an optimistic upper quote bound."""
    if not up_condition or up_condition != down_condition:
        raise CostError("tokens from different markets/conditions")
    _rate(up_fee_rate, exponent)
    _rate(down_fee_rate, exponent)
    other = _d(other_costs)
    if other < 0:
        raise CostError("negative costs")
    u = walk_asks(up, shares)
    d = walk_asks(down, shares)
    if not u["full_depth"] or not d["full_depth"]:
        return {
            "status": "QUOTE_ONLY_NONMONETIZABLE",
            "paper_net_upper_bound_usdc": None,
            "paper_net_lower_bound_usdc": None,
            "reason": "NO_JOINT_DEPTH",
        }
    gross = float(_d(shares)) - u["gross_cost"] - d["gross_cost"]
    fees = _level_fee(u["levels_taken"], up_fee_rate, exponent) + _level_fee(d["levels_taken"], down_fee_rate, exponent)
    return {
        "gross_pair_margin_usdc": gross,
        "paper_net_upper_bound_usdc": gross-fees-float(other),
        "paper_net_lower_bound_usdc": None,
        "up_quote": u,
        "down_quote": d,
        "status": "QUOTE_ONLY_NONMONETIZABLE",
        "reason": "NO_INDEPENDENT_ATOMIC_FILL_PROOF",
    }
