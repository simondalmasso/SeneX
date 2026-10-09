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

def paper_buy_with_market_vintage(*,original_market_bytes,expected_market_sha256,
                                  market_id,source_received_at_ms,t0_ms,
                                  assumed_match_at_ms,expected_oracle_window_seconds,
                                  shares,price,rebate=None,trade_id=None):
    """Bound one hypothetical entry to a *local* original-byte fee schedule.

    A SHA of supplied bytes only checks consistency of this offline fixture.
    It is NOT independent Gamma, execution, fee-version or oracle authority.
    """
    import hashlib
    import json
    base={'net_edge_status':'NET_EDGE_NOT_COMPUTABLE',
          'fee_vintage_status':'UNVERIFIED_FEE',
          'label_authority':'UNVERIFIED','source_admissible':False,
          'fill_proven':False,'realized_pnl_status':'NOT_COMPUTABLE',
          'rebate_credit_decimal':'0'}
    try:
        if (type(original_market_bytes) is not bytes or not original_market_bytes or
            len(original_market_bytes)>100_000 or
            type(expected_market_sha256) is not str or
            hashlib.sha256(original_market_bytes).hexdigest()!=expected_market_sha256):
            raise CostError('original market bytes/SHA not pinned')
        def unique_pairs(pairs):
            result={}
            for k,v in pairs:
                if k in result:raise CostError('duplicate original fee JSON key')
                result[k]=v
            return result
        obj=json.loads(original_market_bytes,object_pairs_hook=unique_pairs,
                       parse_float=Decimal,
                       parse_constant=lambda _: (_ for _ in ()).throw(CostError('invalid JSON constant')))
        if (type(obj) is not dict or not isinstance(market_id,str) or not market_id or
            obj.get('id')!=market_id or obj.get('version')!='v1'):
            raise CostError('unbound market/protocol version')
        if (type(expected_oracle_window_seconds) is not int or
            type(obj.get('oracleWindowSeconds')) is not int or
            obj['oracleWindowSeconds']!=expected_oracle_window_seconds or
            expected_oracle_window_seconds not in (30,60)):
            base['fee_vintage_status']='LABEL_UNVERIFIED'
            raise CostError('unverified original oracle regime')
        if not all(type(t) is int and t>=0 for t in
                   (source_received_at_ms,t0_ms,assumed_match_at_ms)):
            raise CostError('noncanonical timestamp')
        if not source_received_at_ms<=t0_ms<=assumed_match_at_ms:
            raise CostError('source snapshot after T0 or impossible match')
        fee=obj.get('feeSchedule')
        if type(fee) is not dict or set(fee) != {
                'rate','exponent','takerOnly','feesEnabled',
                'effectiveFromMs','effectiveToMs'}:
            raise CostError('original fee schedule incomplete or ambiguous')
        if fee['takerOnly'] is not True or fee['feesEnabled'] is not True:
            raise CostError('not an authorized taker fee schedule')
        if type(fee['exponent']) is not int or fee['exponent']!=1:
            raise CostError('UNVERIFIED_FEE exponent other than 1')
        start,end=fee['effectiveFromMs'],fee['effectiveToMs']
        if (type(start) is not int or type(end) is not int or
            not start<=assumed_match_at_ms<end or start>=end):
            raise CostError('market fee version not valid at hypothetical match time')
        if rebate is not None or trade_id is not None:
            raise CostError('maker rebate/trade ID unverified')
        q,p,r=_d(shares),_d(price),_d(fee['rate'])
        if q<=0 or not 0<p<1 or not 0<=r<=1:
            raise CostError('bad fee vintage quote')
        conditional=paper_buy(shares=shares,price=price,rate=fee['rate'],
                              outcome=1,matched=False)
        unit=(Decimal(conditional['conditional_fee_usdc_decimal'])/q).quantize(
              Decimal('0.00001'),rounding=ROUND_HALF_UP)
        threshold=p+unit
        return {**base,'fee_vintage_status':'CONDITIONAL_DOCUMENTARY_FIXTURE_ONLY',
                'raw_market_sha256':expected_market_sha256,
                'conditional_fee_usdc_decimal':conditional['conditional_fee_usdc_decimal'],
                'conditional_fee_per_share_decimal':str(unit),
                'conditional_entry_cost_per_share_decimal':str(threshold),
                'conditional_break_even_probability_decimal':str(threshold),
                'fee_rounding_basis':'MODELING_ONLY_NOT_VERIFIED_AT_ACTUAL_MATCH'}
    except (CostError,TypeError,KeyError,ValueError,UnicodeError,ArithmeticError):
        return base
