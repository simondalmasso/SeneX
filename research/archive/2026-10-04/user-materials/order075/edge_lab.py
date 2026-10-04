from __future__ import annotations
import asyncio, hashlib, json, math, time, urllib.parse
from dataclasses import dataclass
from typing import Any
import aiohttp

SCHEMA_VERSION='order075-research-v1'
COLLECTOR_VERSION='order075-edge-lab-v1'
POLY_GAMMA='https://gamma-api.polymarket.com'
POLY_CLOB='https://clob.polymarket.com'
POLY_WS='wss://ws-subscriptions-clob.polymarket.com/ws/market'
BINANCE_WS='wss://data-stream.binance.vision:443/stream?streams=' + '/'.join([
 'btcusdt@bookTicker','btcusdt@aggTrade','btcusdt@depth5@100ms','btcusdt@kline_1s',
 'ethusdt@bookTicker','ethusdt@aggTrade','ethusdt@depth5@100ms','ethusdt@kline_1s'])
UA='SENEX-ORDER075-PAPER-SHADOW/1.0'


def now_ms()->int: return time.time_ns()//1_000_000

def key(*parts:Any)->str:
    return hashlib.sha256('|'.join('' if p is None else str(p) for p in parts).encode()).hexdigest()

def loads_field(v):
    if isinstance(v,str):
        try:return json.loads(v)
        except:return v
    return v

def levels(book:dict, side:str):
    out=[]
    for z in book.get(side) or []:
        try:
            p=float(z['price']); q=float(z['size'])
            if p>0 and q>0: out.append((p,q))
        except: pass
    return sorted(out,key=lambda x:x[0],reverse=(side=='bids'))

def book_metrics(book:dict):
    b=levels(book,'bids'); a=levels(book,'asks')
    return {
      'best_bid':b[0][0] if b else None,'best_ask':a[0][0] if a else None,
      'bid_depth_5':sum(q for _,q in b[:5]),'ask_depth_5':sum(q for _,q in a[:5]),
      'bids':b,'asks':a,'hash':book.get('hash'),'source_ts_ms':int(book.get('timestamp') or 0) or None
    }

def fee_per_share(price:float, fee_rate:float)->float:
    # Official Polymarket formula: C * feeRate * p * (1-p); here C=1 share.
    x=max(0.0, fee_rate*price*(1.0-price))
    # protocol fee precision is 5 decimals at trade level; per-share retained unrounded here,
    # total fees are rounded to five decimals after multiplication by shares.
    return x

def consume(levels_:list[tuple[float,float]], shares:float, fee_rate:float):
    rem=max(0.0,float(shares)); filled=notional=fee=0.0
    for p,q in levels_:
        take=min(rem,q)
        if take<=0: continue
        filled+=take; notional+=take*p; fee+=take*fee_per_share(p,fee_rate); rem-=take
        if rem<=1e-12:break
    fee=round(fee,5)
    return {'requested':shares,'filled':filled,'residual':rem,'notional':notional,'fee':fee,'vwap':notional/filled if filled else None}

def pair_capacity(yes_book:dict,no_book:dict,fee_rate:float):
    ya=book_metrics(yes_book)['asks']; na=book_metrics(no_book)['asks']
    i=j=0; yr=ya[0][1] if ya else 0; nr=na[0][1] if na else 0
    shares=notional=fees=0.0
    while i<len(ya) and j<len(na):
        py,qy=ya[i]; pn,qn=na[j]
        q=min(yr,nr)
        marginal=py+pn+fee_per_share(py,fee_rate)+fee_per_share(pn,fee_rate)
        if q<=0 or marginal>=1.0-1e-12: break
        shares+=q; notional+=q*(py+pn); fees+=q*(fee_per_share(py,fee_rate)+fee_per_share(pn,fee_rate))
        yr-=q;nr-=q
        if yr<=1e-12:
            i+=1;yr=ya[i][1] if i<len(ya) else 0
        if nr<=1e-12:
            j+=1;nr=na[j][1] if j<len(na) else 0
    return {'shares':shares,'notional':notional,'fees':round(fees,5),'capacity_usd':notional+round(fees,5)}

def pair_snapshot(yes_book:dict,no_book:dict,fee_rate:float,shares:float):
    ym=book_metrics(yes_book); nm=book_metrics(no_book)
    y=consume(ym['asks'],shares,fee_rate); n=consume(nm['asks'],shares,fee_rate)
    matched=min(y['filled'],n['filled'])
    if matched<=0:
        return {'matched':0.0,'legging_failure':True,'net_edge_bps':None}
    # Recompute to matched quantity so costs compare on equal complete sets.
    y=consume(ym['asks'],matched,fee_rate); n=consume(nm['asks'],matched,fee_rate)
    best_gross=(ym['best_ask'] or 0)+(nm['best_ask'] or 0)
    exec_notional=y['notional']+n['notional']
    gross_cost=best_gross
    slippage_cost=(exec_notional/matched)-best_gross
    fee_cost=(y['fee']+n['fee'])/matched
    net_cost=exec_notional/matched+fee_cost
    edge=1.0-net_cost
    return {
      'matched':matched,'yes':y,'no':n,'yes_best_bid':ym['best_bid'],'yes_best_ask':ym['best_ask'],
      'no_best_bid':nm['best_bid'],'no_best_ask':nm['best_ask'],'yes_depth':sum(q for _,q in ym['asks']),
      'no_depth':sum(q for _,q in nm['asks']),'gross_cost':gross_cost,'fee_cost':fee_cost,
      'slippage_cost':slippage_cost,'net_cost':net_cost,'net_edge_bps':edge*10000,
      'legging_failure':y['residual']>1e-9 or n['residual']>1e-9
    }

async def get_json(session:aiohttp.ClientSession,url:str):
    async with session.get(url,headers={'User-Agent':UA},timeout=aiohttp.ClientTimeout(total=10)) as r:
        r.raise_for_status(); return await r.json()

async def discover_current(session:aiohttp.ClientSession, at_s:int|None=None):
    at_s=at_s or int(time.time()); out=[]
    for asset,prefix in [('BTC','btc'),('ETH','eth')]:
      for h,label in [(300,'5m'),(900,'15m')]:
        aligned=(at_s//h)*h
        found=None
        for start in [aligned,aligned-h,aligned+h]:
          slug=f'{prefix}-updown-{label}-{start}'
          try:
            m=await get_json(session,f'{POLY_GAMMA}/markets/slug/{slug}')
          except Exception: continue
          if m.get('active') is True and m.get('closed') is not True and m.get('acceptingOrders') is True:
            found=(start,m);break
        if not found: continue
        start,m=found; toks=loads_field(m.get('clobTokenIds')) or []; outs=loads_field(m.get('outcomes')) or []
        if len(toks)!=2 or len(outs)!=2: continue
        om={str(o).lower():str(t) for o,t in zip(outs,toks)}
        yes=om.get('up') or om.get('yes'); no=om.get('down') or om.get('no')
        if not yes or not no: continue
        fs=m.get('feeSchedule') or {}; rate=float(fs.get('rate') or (0.07 if m.get('feesEnabled') else 0))
        out.append({'asset':asset,'symbol':asset+'USDT','horizon_s':h,'label':label,'market_id':str(m['id']),
          'condition_id':str(m['conditionId']),'token_yes':yes,'token_no':no,'event_start_ms':start*1000,
          'event_expiry_ms':(start+h)*1000,'resolution_source':m.get('resolutionSource'),'question':m.get('question'),
          'slug':m.get('slug'),'fees_enabled':bool(m.get('feesEnabled')),'taker_fee_rate':rate,'raw':m})
    return out

async def fetch_book(session:aiohttp.ClientSession, token:str):
    return await get_json(session,f'{POLY_CLOB}/book?token_id={urllib.parse.quote(token,safe="")}')

async def fetch_pair_books(session, market):
    y,n=await asyncio.gather(fetch_book(session,market['token_yes']),fetch_book(session,market['token_no']))
    return y,n

def microprice(bid, bq, ask, aq):
    vals=[bid,bq,ask,aq]
    if any(v is None for v in vals) or bq+aq<=0:return None
    return (ask*bq+bid*aq)/(bq+aq)