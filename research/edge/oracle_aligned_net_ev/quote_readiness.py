"""Static CLOB quote-side gate; no fill probability, no realized trading."""
from decimal import Decimal, InvalidOperation
import re

class QuoteError(ValueError):pass

def _d(x):
    if type(x) not in (str,int):raise QuoteError('noncanonical Decimal input')
    try:d=Decimal(str(x))
    except InvalidOperation as exc:raise QuoteError('bad Decimal') from exc
    if not d.is_finite():raise QuoteError('nonfinite Decimal')
    return d

def _validate_levels(asks,bids,tick):
    if not isinstance(asks,list) or not isinstance(bids,list) or tick<=0 or tick>=1:
        raise QuoteError('invalid levels/tick')
    price_sets=[]
    for levels in (asks,bids):
        ps=[]
        for z in levels:
            if not isinstance(z,dict):raise QuoteError('malformed level')
            p,q=_d(z.get('price')),_d(z.get('size'))
            if not 0<p<1 or q<=0 or p%tick!=0:raise QuoteError('bad price-grid/size')
            ps.append(p)
        price_sets.append(ps)
    if price_sets[0] and price_sets[1] and max(price_sets[1])>=min(price_sets[0]):
        raise QuoteError('crossed book')

def classify_buy(book,*,condition_id,asset_id,candidate_shares,price_cap,observed_at_ms,max_age_ms,original_sha256):
    base={'fill_proven':False,'source_admissible':False,'execution_evidence':'FILL_UNVERIFIED',
          'original_sha256':original_sha256,'condition_id':condition_id,'asset_id':asset_id,
          'realized_pnl_status':'NOT_COMPUTABLE'}
    def blocked(status,reason):return {**base,'status':status,'reason':reason}
    if type(observed_at_ms) is not int or type(max_age_ms) is not int or max_age_ms<0:
        raise QuoteError('noncanonical local wall clock/tolerance')
    if type(book.get('timestamp')) is not int or book['timestamp']>observed_at_ms:
        return blocked('NO_BOOK','unknown/future wire timestamp')
    if observed_at_ms-book['timestamp']>max_age_ms:
        return blocked('STALE_BOOK','source clock older than allowed quote age')
    if (book.get('market')!=condition_id or book.get('asset_id')!=asset_id or
        not isinstance(book.get('hash'),str) or not book['hash'] or
        not re.fullmatch('[0-9a-f]{64}',original_sha256)):
        return blocked('NO_BOOK','missing provenance/hash/identity')
    try:
        tick=_d(book.get('tick_size'));minimum=_d(book.get('min_order_size'))
        q=_d(candidate_shares);cap=_d(price_cap)
        _validate_levels(book.get('asks'),book.get('bids'),tick)
    except (QuoteError,TypeError,KeyError):
        return blocked('NO_BOOK','invalid tick, size, price grid or levels')
    if q<minimum or minimum<=0 or q<=0 or not 0<cap<1:
        return blocked('NO_BOOK','below venue min / invalid candidate')
    asks=sorted(((_d(z['price']),_d(z['size'])) for z in book['asks']),key=lambda x:x[0])
    spent=Decimal(0);remaining=q
    for p,level_size in asks:
        if p>cap:break
        taken=min(remaining,level_size)
        if taken<0:raise QuoteError('negative quote walk')
        spent+=taken*p;remaining-=taken
        if remaining==0:break
    if remaining:
        return blocked('NO_BOOK','partial or out-of-cap displayed depth')
    return {**base,'status':'QUOTE_ONLY_NONMONETIZABLE','quote_available':True,
            'conditional_premium_decimal':str(spent),
            'candidate_shares_decimal':str(q),'min_order_size_decimal':str(minimum),
            'tick_size_decimal':str(tick),'observed_at_ms':observed_at_ms,
            'book_timestamp_ms':book['timestamp'],'quote_hash':book['hash']}

def classify_pair(yes,no):
    if any(x.get('status') != 'QUOTE_ONLY_NONMONETIZABLE' or x.get('quote_available') is not True for x in (yes,no)):
        return {'status':'NO_BOOK','fill_proven':False,'reason':'one or both legs lack validated quote'}
    if yes.get('condition_id')!=no.get('condition_id') or yes.get('asset_id')==no.get('asset_id'):
        return {'status':'NO_BOOK','fill_proven':False}
    return {'status':'NON_ATOMIC_PAIR','fill_proven':False,'simultaneous_fill_proven':False,
            'reason':'two snapshots are independent; no atomic matching proof'}

def classify_sell(book,*,condition_id,asset_id,candidate_shares,price_floor,
                  verified_inventory_shares,observed_at_ms,max_age_ms,original_sha256):
    """Inventory-gated hypothetical bid-side sale; no naked/synthetic short."""
    try:
        qty=_d(candidate_shares);inventory=_d(verified_inventory_shares)
    except QuoteError:
        return {'status':'ABSTAIN_NO_INVENTORY','fill_proven':False}
    if qty<=0 or inventory<qty:
        return {'status':'ABSTAIN_NO_INVENTORY','fill_proven':False}
    if type(book.get('timestamp')) is not int or book['timestamp']>observed_at_ms or observed_at_ms-book['timestamp']>max_age_ms:
        return {'status':'STALE_BOOK','fill_proven':False}
    if (book.get('market')!=condition_id or book.get('asset_id')!=asset_id or
        not isinstance(book.get('hash'),str) or not book['hash'] or
        not re.fullmatch('[0-9a-f]{64}',original_sha256)):
        return {'status':'NO_BOOK','fill_proven':False}
    try:
        tick=_d(book.get('tick_size'));minimum=_d(book.get('min_order_size'))
        floor=_d(price_floor)
        _validate_levels(book.get('asks'),book.get('bids'),tick)
        if qty<minimum or not 0<floor<1:raise QuoteError('minimum/floor')
        bids=sorted(((_d(z['price']),_d(z['size'])) for z in book['bids']),reverse=True)
        remaining=qty;proceeds=Decimal(0)
        for p,size in bids:
            if p<floor:break
            used=min(size,remaining);proceeds+=p*used;remaining-=used
            if not remaining:break
        if remaining:raise QuoteError('insufficient bid depth')
    except QuoteError:
        return {'status':'NO_BOOK','fill_proven':False}
    return {'status':'QUOTE_ONLY_NONMONETIZABLE','fill_proven':False,
        'conditional_sale_proceeds_decimal':str(proceeds),
        'inventory_source':'DECLARED_UNVERIFIED','original_sha256':original_sha256}
