"""M17 paper-only quote economics with Decimal and explicit market fee inputs.

No order placements, wallets, fills, or universal fee schedules. Float outputs
exist only for legacy presentation; all internal sums and audit strings use Decimal.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


class CostError(ValueError):
    """Unknown fee schedule, invalid quantity, or nonexecutable quote."""


FEE_QUANTUM = Decimal("0.00001")
QUOTE_QUANTUM = Decimal("0.00001")


def _d(value: object) -> Decimal:
    try:
        if isinstance(value, bool):
            raise TypeError()
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CostError("invalid numeric input") from exc
    if not result.is_finite():
        raise CostError("non-finite numeric input")
    return result


def _rate(rate: object, exponent: object) -> Decimal:
    if rate is None or type(exponent) is not int or exponent != 1:
        raise CostError("UNVERIFIED_FEE: require sampled rate and exponent=1")
    result = _d(rate)
    if result < 0 or result > 1:
        raise CostError("invalid fee rate")
    return result


def fee_usdc_decimal(
    shares: object, price: object, *, fee_rate: object, exponent: int
) -> Decimal:
    """Synthetic crypto_fees_v2 curve for explicitly supplied rate/version.

    Polymarket states five decimals and a minimum nonzero 0.00001 USDC;
    exact tie-breaking is not independently verified. ROUND_HALF_UP is a
    prereview modeling convention only; no admissible execution claim.
    """
    rate, qty, p = _rate(fee_rate, exponent), _d(shares), _d(price)
    if qty < 0 or not Decimal(0) < p < Decimal(1):
        raise CostError("invalid quantity or price")
    raw = qty * rate * p * (Decimal(1) - p)
    return raw.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)


def fee_usdc(shares: object, price: object, *, fee_rate: object, exponent: int) -> float:
    return float(fee_usdc_decimal(shares, price, fee_rate=fee_rate, exponent=exponent))


def walk_asks(levels: list, desired_shares: object) -> dict:
    """Exact Decimal ask walk; quote-only, never simulated actual fills."""
    needed = _d(desired_shares)
    if needed <= 0 or not isinstance(levels, list):
        raise CostError("missing/invalid ask depth")
    parsed = []
    for level in levels:
        if not isinstance(level, dict) or "price" not in level or "size" not in level:
            raise CostError("invalid depth level")
        p, q = _d(level["price"]), _d(level["size"])
        if not Decimal(0) < p < Decimal(1) or q <= 0:
            raise CostError("invalid ask level")
        parsed.append((p,q))
    parsed.sort(key=lambda item:item[0])
    filled, spent, used = Decimal(0), Decimal(0), []
    for p, available in parsed:
        take = min(needed-filled, available)
        if take <= 0:
            break
        filled += take
        spent += p*take
        used.append({"price":float(p), "shares":float(take),
                     "price_decimal":str(p), "shares_decimal":str(take)})
        if filled == needed:
            break
    return {
        "filled_shares":float(filled),
        "filled_shares_decimal":str(filled),
        "gross_cost":float(spent),
        "gross_cost_exact_decimal":str(spent),
        "gross_cost_decimal":str(spent.quantize(QUOTE_QUANTUM, rounding=ROUND_HALF_UP)),
        "vwap_ask":float(spent/filled) if filled else None,
        "full_depth":filled == needed,
        "levels_taken":used,
        "evidence":"PAPER_QUOTE_ONLY",
    }


def _level_fee(levels: list, rate: object, exponent: int) -> Decimal:
    return sum((
        fee_usdc_decimal(level["shares_decimal"],level["price_decimal"],
                         fee_rate=rate,exponent=exponent)
        for level in levels
    ), Decimal(0)).quantize(FEE_QUANTUM)


def hold_quote_net(
    levels: list, shares: object, *, outcome: int, fee_rate: object,
    exponent: int, slippage_usdc: object = 0
) -> dict:
    """Hypothetical payoff, never independently executable alpha."""
    _rate(fee_rate,exponent)
    if type(outcome) is not int or outcome not in (0,1):
        raise CostError("outcome must be binary")
    slip = _d(slippage_usdc)
    if slip < 0:
        raise CostError("negative slippage")
    quote = walk_asks(levels,shares)
    if not quote["full_depth"]:
        raise CostError("PARTIAL_DEPTH: quote not fully obtainable")
    filled = _d(quote["filled_shares_decimal"])
    cost = sum((_d(z["price_decimal"])*_d(z["shares_decimal"])
                for z in quote["levels_taken"]), Decimal(0))
    fee = _level_fee(quote["levels_taken"],fee_rate,exponent)
    net = filled*Decimal(outcome)-cost-fee-slip
    return {**quote,"fee_usdc":float(fee),"fee_usdc_decimal":str(fee),
            "paper_net_usdc":float(net),
            "paper_net_usdc_decimal":str(net.quantize(QUOTE_QUANTUM,rounding=ROUND_HALF_UP))}


def paired_quote_upper_bound(
    up: list, down: list, shares: object, *, up_condition: str, down_condition: str,
    up_fee_rate: object, down_fee_rate: object, exponent: int,
    other_costs: object = 0
) -> dict:
    if not up_condition or up_condition != down_condition:
        raise CostError("market conditions are not identical")
    _rate(up_fee_rate,exponent)
    _rate(down_fee_rate,exponent)
    other = _d(other_costs)
    if other < 0:
        raise CostError("negative other costs")
    u, d = walk_asks(up,shares),walk_asks(down,shares)
    if not u["full_depth"] or not d["full_depth"]:
        return {"status":"QUOTE_ONLY_NONMONETIZABLE",
                "paper_net_upper_bound_usdc":None,"paper_net_lower_bound_usdc":None,
                "reason":"NO_JOINT_DEPTH"}
    gross = _d(shares)-sum((_d(z["price_decimal"])*_d(z["shares_decimal"])
          for z in u["levels_taken"]+d["levels_taken"]), Decimal(0))
    fees=_level_fee(u["levels_taken"],up_fee_rate,exponent)+_level_fee(
        d["levels_taken"],down_fee_rate,exponent)
    net=gross-fees-other
    return {"gross_pair_margin_usdc":float(gross),
            "gross_pair_margin_decimal":str(gross),
            "paper_net_upper_bound_usdc":float(net),
            "paper_net_upper_bound_decimal":str(net),
            "paper_net_lower_bound_usdc":None,
            "up_quote":u,"down_quote":d,
            "status":"QUOTE_ONLY_NONMONETIZABLE",
            "reason":"NO_INDEPENDENT_ATOMIC_FILL_PROOF"}
