"""Cost identities for hypothetical static quotes, NEVER fills or realized P&L."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

class CostError(ValueError):pass

def _d(value):
    if type(value) not in (str,int):raise CostError('unverified numeric input type')
    try:d=Decimal(str(value))
    except InvalidOperation as exc:raise CostError('invalid decimal') from exc
    if not d.is_finite():raise CostError('nonfinite decimal')
    return d

def paper_buy(*,shares,price,rate=None,outcome=None,matched=False,slippage=0,rebate=None):
    if rebate is not None:raise CostError('maker rebate not proven; no income permitted')
    if type(matched) is not bool:raise CostError('matched flag must be explicit hypothesis')
    if type(outcome) is not int or outcome not in (0,1):raise CostError('binary outcome required')
    if rate is None:raise CostError('UNVERIFIED_FEE: sourced rate required')
    q,p,r,slip=map(_d,(shares,price,rate,slippage))
    if q<=0 or not 0<p<1 or not 0<=r<=1 or slip<0:
        raise CostError('negative/out-of-range quote or fee')
    premium=q*p
    raw_fee=q*r*p*(1-p)
    fee=raw_fee.quantize(Decimal('0.00001'),rounding=ROUND_HALF_UP)
    payoff=q*Decimal(outcome)
    hold_net=payoff-premium-fee-slip
    return {'status':'HYPOTHETICAL_MATCH_NOT_VERIFIED' if matched else 'QUOTE_ONLY_NONMONETIZABLE',
       'fill_proven':False,'source_admissible':False,'rebate_credit_decimal':'0',
       'conditional_fee_usdc_decimal':str(fee),
       'fee_to_premium_decimal':str(raw_fee/premium),
       'conditional_premium_usdc_decimal':str(premium),
       'conditional_slippage_usdc_decimal':str(slip),
       'conditional_hold_net_usdc_decimal':str(hold_net),
       'conditional_return_on_premium_decimal':str(hold_net/premium),
       'charged_fee_usdc_decimal':str(fee if matched else Decimal(0)),
       'charged_slippage_usdc_decimal':str(slip if matched else Decimal(0)),
       'realized_pnl_authority':'NONE',
       'rounding_basis':'MODELING_ONLY_HALF_UP_NOT_VENUE_VERIFIED',
       'round_trip_status':'NOT_COMPUTABLE_WITHOUT_SELL_QUOTE_AND_INVENTORY'}