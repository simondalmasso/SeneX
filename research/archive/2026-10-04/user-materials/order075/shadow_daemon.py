from __future__ import annotations
import asyncio, collections, gzip, hashlib, json, math, os, signal, statistics, time
from pathlib import Path
from typing import Any
import aiohttp, websockets
from edge_lab import *

RTDS='wss://ws-live-data.polymarket.com'
WORKER='https://senex-order075-edge-lab.simondalmasso44.workers.dev'
JOIN_TOLERANCE_MS=413
BOOK_SAMPLE_MS=5000
BINANCE_SAMPLE_MS=1000
EVAL_MS=1000
FULL_BOOK_RESYNC_MS=30000
PAPER_LATENCY_MS=350  # conservative: > measured Poly p99 313 ms initial preflight
MAX_STALE_POLY_MS=1500
MAX_STALE_BINANCE_MS=1000
CAPITAL_LEVELS=(100.0,500.0,1000.0)
RUN_ID='order075-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:8]
ROOT=Path(__file__).resolve().parent
RAW=ROOT/'raw'; RAW.mkdir(exist_ok=True)
STATE_DIR=ROOT/'state'; STATE_DIR.mkdir(exist_ok=True)
TOKEN=(ROOT/'private/ingest_token').read_text().strip()

def finite(v):
    if v is None or v == '': return None
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (TypeError,ValueError):
        return None

class Lab:
    def __init__(self):
        self.stop=asyncio.Event(); self.markets=[]; self.token_meta={}; self.books={}
        self.binance={}; self.binance_hist={s:collections.deque(maxlen=300) for s in ['BTCUSDT','ETHUSDT']}
        self.binance_trades={s:collections.deque(maxlen=20000) for s in ['BTCUSDT','ETHUSDT']}
        self.chainlink={a:collections.deque(maxlen=1000) for a in ['BTC','ETH']}
        self.poly_last={}; self.poly_mid_hist={}; self.ingest_q=asyncio.Queue(maxsize=20000); self.counters=collections.Counter()
        self.last_registry={}; self.started_ms=now_ms(); self.last_errors=collections.deque(maxlen=100)
        self.clock_offset_ms=0.0; self.network_latency_ms=60.0
        self.session=None; self.raw_handles={}; self.raw_write_counts=collections.Counter()

    def log_raw(self,kind,obj):
        day=time.strftime('%Y%m%d',time.gmtime())
        hk=(kind,day); f=self.raw_handles.get(hk)
        if f is None:
            f=gzip.open(RAW/f'{kind}-{day}.ndjson.gz','at',encoding='utf-8',compresslevel=5); self.raw_handles[hk]=f
        f.write(json.dumps(obj,separators=(',',':'),ensure_ascii=False)+'\n'); self.raw_write_counts[hk]+=1
        if self.raw_write_counts[hk]%100==0:f.flush()

    def close_raw(self):
        for f in self.raw_handles.values():
            try:f.flush();f.close()
            except:pass
        self.raw_handles.clear()

    async def emit(self,row):
        try:self.ingest_q.put_nowait(row)
        except asyncio.QueueFull:
            self.counters['ingest_queue_drop']+=1; self.log_raw('ingest_queue_drop',row)

    async def ingest_loop(self):
        headers={'Authorization':'Bearer '+TOKEN,'content-type':'application/json'}
        while not self.stop.is_set() or not self.ingest_q.empty():
            batch=[]
            try: batch.append(await asyncio.wait_for(self.ingest_q.get(),timeout=1))
            except asyncio.TimeoutError: continue
            while len(batch)<200 and not self.ingest_q.empty(): batch.append(self.ingest_q.get_nowait())
            ok=False
            for attempt in range(5):
                try:
                    async with self.session.post(WORKER+'/ingest',headers=headers,json={'rows':batch},timeout=aiohttp.ClientTimeout(total=15)) as r:
                        text_=await r.text()
                        if r.status==200:
                            ok=True; self.counters['d1_rows_ingested']+=len(batch); break
                        raise RuntimeError(f'HTTP_{r.status}:{text_[:120]}')
                except Exception as e:
                    self.last_errors.append({'at_ms':now_ms(),'where':'ingest','error':str(e)[:200]}); await asyncio.sleep(min(8,2**attempt))
            if not ok:
                self.counters['d1_ingest_failed_rows']+=len(batch); self.log_raw('d1_spool',{'rows':batch})
            for _ in batch:self.ingest_q.task_done()

    async def run_row(self,status='RUNNING'):
        recv=now_ms()
        await self.emit({'kind':'run','run_id':RUN_ID,'receive_ts_ms':recv,'source':'SENEX_ORDER075','schema_version':SCHEMA_VERSION,
          'started_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(self.started_ms/1000)),'collector_version':COLLECTOR_VERSION,
          'status':status,'config':{'join_tolerance_ms':JOIN_TOLERANCE_MS,'book_sample_ms':BOOK_SAMPLE_MS,'binance_sample_ms':BINANCE_SAMPLE_MS,
          'paper_latency_ms':PAPER_LATENCY_MS,'polymarket_order_placement':False,'binance_order_placement':False,'orders':False,'live_capital_locked':True}})

    async def registry_rows(self,markets):
        recv=now_ms()
        for m in markets:
            rk=key(RUN_ID,m['condition_id'])
            if rk in self.last_registry: continue
            self.last_registry[rk]=recv
            await self.emit({'kind':'market_registry','key':rk,'run_id':RUN_ID,'observed_at_ms':recv,'receive_ts_ms':recv,'source':'POLYMARKET_GAMMA',
              'schema_version':SCHEMA_VERSION,**{k:m[k] for k in ['asset','horizon_s','market_id','condition_id','token_yes','token_no','event_start_ms','event_expiry_ms','resolution_source','question','slug','fees_enabled','taker_fee_rate']},
              'payload':{'gamma':m.get('raw'),'event_window_source':'slug_epoch_plus_horizon','gamma_startDate_not_used_as_event_start':True}})

    async def discover_loop(self):
        while not self.stop.is_set():
            try:
                ms=await discover_current(self.session)
                ms=[m for m in ms if 'data.chain.link/streams/' in str(m.get('resolution_source') or '') and 'twap' in str(m.get('resolution_source') or '').lower()]
                if len(ms)==4:
                    self.markets=ms; self.token_meta={m['token_yes']:{**m,'outcome':'YES'} for m in ms}; self.token_meta.update({m['token_no']:{**m,'outcome':'NO'} for m in ms})
                    await self.registry_rows(ms); self.counters['discovery_ok']+=1
                else:self.counters['discovery_incomplete']+=1
            except Exception as e:self.last_errors.append({'at_ms':now_ms(),'where':'discover','error':str(e)[:200]})
            await asyncio.sleep(10)

    def apply_book(self,d,recv):
        token=str(d.get('asset_id') or '')
        if token not in self.token_meta:return
        b={float(x['price']):float(x['size']) for x in d.get('bids') or [] if float(x.get('size') or 0)>0}
        a={float(x['price']):float(x['size']) for x in d.get('asks') or [] if float(x.get('size') or 0)>0}
        self.books[token]={'bids':b,'asks':a,'source_ts_ms':int(d.get('timestamp') or recv),'receive_ts_ms':recv,'hash':d.get('hash'),'full_sync_ms':recv}

    def apply_price_change(self,d,recv):
        ts=int(d.get('timestamp') or recv)
        for ch in d.get('price_changes') or []:
            token=str(ch.get('asset_id') or '')
            if token not in self.token_meta or token not in self.books:continue
            p=float(ch.get('price') or 0); q=float(ch.get('size') or 0); side=str(ch.get('side') or '').upper()
            levels_=self.books[token]['bids' if side=='BUY' else 'asks']
            if q<=0:levels_.pop(p,None)
            else:levels_[p]=q
            self.books[token]['source_ts_ms']=ts;self.books[token]['receive_ts_ms']=recv;self.books[token]['hash']=ch.get('hash') or self.books[token].get('hash')

    def state_book_dict(self,token,depth=50):
        s=self.books.get(token)
        if not s:return None
        bids=sorted(s['bids'].items(),reverse=True)[:depth];asks=sorted(s['asks'].items())[:depth]
        return {'bids':[{'price':str(p),'size':str(q)} for p,q in bids],'asks':[{'price':str(p),'size':str(q)} for p,q in asks],
          'timestamp':str(s['source_ts_ms']),'hash':s.get('hash')}

    async def poly_ws_loop(self):
        while not self.stop.is_set():
            try:
                if not self.markets: await asyncio.sleep(1); continue
                tokens=list(self.token_meta)
                async with websockets.connect(POLY_WS,open_timeout=10,close_timeout=3,ping_interval=None,max_size=6_000_000) as ws:
                    await ws.send(json.dumps({'assets_ids':tokens,'type':'market','custom_feature_enabled':True},separators=(',',':')))
                    self.counters['poly_ws_connect']+=1; opened=time.monotonic(); last_ping=opened
                    while not self.stop.is_set() and time.monotonic()-opened<20:
                        if time.monotonic()-last_ping>=5: await ws.send('PING');last_ping=time.monotonic()
                        try:msg=await asyncio.wait_for(ws.recv(),timeout=1)
                        except asyncio.TimeoutError:continue
                        recv=now_ms()
                        if msg=='PONG':self.counters['poly_pong']+=1;continue
                        try:d=json.loads(msg)
                        except:continue
                        seq=d if isinstance(d,list) else [d]
                        for ev in seq:
                            if not isinstance(ev,dict):continue
                            typ=ev.get('event_type')
                            if typ=='book':self.apply_book(ev,recv)
                            elif typ=='price_change':self.apply_price_change(ev,recv)
                            elif typ in ('last_trade_price','best_bid_ask'):
                                token=str(ev.get('asset_id') or ''); meta=self.token_meta.get(token)
                                if meta:
                                    st=int(ev.get('timestamp') or recv); self.poly_last[token]={'source_ts_ms':st,'receive_ts_ms':recv,'event':ev}
                                    row={'kind':'poly_event','key':key(RUN_ID,token,typ,st,ev.get('transaction_hash'),ev.get('price'),ev.get('size')),
                                      'run_id':RUN_ID,'source_ts_ms':st,'receive_ts_ms':recv,'source':'POLYMARKET_CLOB_WS','schema_version':SCHEMA_VERSION,
                                      'event_type':typ,'asset':meta['asset'],'horizon_s':meta['horizon_s'],'market_id':meta['market_id'],'condition_id':meta['condition_id'],
                                      'token_id':token,'outcome':meta['outcome'],'price':finite(ev.get('price')),'size':finite(ev.get('size')),'side':ev.get('side'),
                                      'best_bid':finite(ev.get('best_bid')),'best_ask':finite(ev.get('best_ask')),'payload':ev}
                                    await self.emit(row); self.log_raw('poly-events',{'receive_ts_ms':recv,'payload':ev})
                            self.counters['poly_events']+=1
            except Exception as e:
                self.counters['poly_ws_errors']+=1;self.last_errors.append({'at_ms':now_ms(),'where':'poly_ws','error':str(e)[:200]});await asyncio.sleep(1)

    async def rtds_asset_loop(self,asset):
        sym='btc/usd' if asset=='BTC' else 'eth/usd'
        filt=json.dumps({'symbol':sym},separators=(',',':'))
        while not self.stop.is_set():
            try:
                async with websockets.connect(RTDS,open_timeout=10,close_timeout=3,ping_interval=None,max_size=6_000_000) as ws:
                    await ws.send(json.dumps({'action':'subscribe','subscriptions':[{'topic':'crypto_prices_chainlink','type':'update','filters':filt}]},separators=(',',':')))
                    self.counters[f'rtds_{asset}_connect']+=1; last_ping=time.monotonic()
                    while not self.stop.is_set():
                        if time.monotonic()-last_ping>=5:await ws.send('PING');last_ping=time.monotonic()
                        try:msg=await asyncio.wait_for(ws.recv(),timeout=1)
                        except asyncio.TimeoutError:continue
                        if msg in ('PONG',''):continue
                        recv=now_ms()
                        try:d=json.loads(msg)
                        except:continue
                        if d.get('topic')!='crypto_prices_chainlink':continue
                        p=d.get('payload') or {}; got=str(p.get('symbol') or '').lower()
                        if got!=sym:continue
                        st=int(p.get('timestamp') or d.get('timestamp') or recv); val=finite(p.get('value'))
                        if val is None:continue
                        self.chainlink[asset].append((st,val,recv));self.counters[f'chainlink_{asset}_updates']+=1
                        self.log_raw('chainlink',{'receive_ts_ms':recv,'payload':d})
            except Exception as e:self.counters[f'rtds_{asset}_errors']+=1;self.last_errors.append({'at_ms':now_ms(),'where':f'rtds_{asset}','error':str(e)[:200]});await asyncio.sleep(1)

    async def binance_loop(self):
        while not self.stop.is_set():
            try:
                async with websockets.connect(BINANCE_WS,open_timeout=10,close_timeout=3,ping_interval=20,ping_timeout=20,max_size=6_000_000) as ws:
                    self.counters['binance_ws_connect']+=1
                    while not self.stop.is_set():
                        try:msg=await asyncio.wait_for(ws.recv(),timeout=2)
                        except asyncio.TimeoutError:continue
                        recv=now_ms()
                        try:x=json.loads(msg); stream=x.get('stream','');d=x.get('data') or {}
                        except:continue
                        sym=str(d.get('s') or stream.split('@')[0]).upper();
                        if sym not in self.binance: self.binance[sym]={}
                        if stream.endswith('@bookTicker'):
                            bid=finite(d.get('b'));bq=finite(d.get('B'));ask=finite(d.get('a'));aq=finite(d.get('A'))
                            if bid and ask:
                                mid=(bid+ask)/2; mp=microprice(bid,bq,ask,aq)
                                self.binance[sym].update({'bid':bid,'bq':bq,'ask':ask,'aq':aq,'mid':mid,'microprice':mp,'receive_ts_ms':recv,'source_ts_ms':None,'stream':'bookTicker'})
                                self.binance_hist[sym].append((recv,mid));self.counters['binance_bookticker']+=1
                        elif stream.endswith('@aggTrade'):
                            st=int(d.get('T') or d.get('E') or recv);px=finite(d.get('p'));q=finite(d.get('q'));side='SELL' if d.get('m') else 'BUY'
                            self.binance_trades[sym].append((st,px,q,side,recv));self.counters['binance_trades']+=1
                            self.log_raw('binance-trades',{'receive_ts_ms':recv,'stream':stream,'payload':d})
                        elif '@depth5@' in stream:
                            self.binance[sym]['depth5']=d;self.binance[sym]['depth_receive_ts_ms']=recv;self.counters['binance_depth']+=1
                        elif '@kline_1s' in stream:self.binance[sym]['kline']=d;self.binance[sym]['kline_receive_ts_ms']=recv;self.counters['binance_kline']+=1
            except Exception as e:self.counters['binance_ws_errors']+=1;self.last_errors.append({'at_ms':now_ms(),'where':'binance_ws','error':str(e)[:200]});await asyncio.sleep(1)

    async def full_resync_loop(self):
        while not self.stop.is_set():
            try:
                for m in list(self.markets):
                    y,n=await fetch_pair_books(self.session,m);recv=now_ms();self.apply_book({**y,'asset_id':m['token_yes']},recv);self.apply_book({**n,'asset_id':m['token_no']},recv)
                self.counters['full_book_resync']+=1
            except Exception as e:self.counters['full_book_resync_errors']+=1;self.last_errors.append({'at_ms':now_ms(),'where':'resync','error':str(e)[:200]})
            await asyncio.sleep(FULL_BOOK_RESYNC_MS/1000)

    def hist_return(self,sym,now_,seconds):
        h=self.binance_hist.get(sym); cur=(self.binance.get(sym) or {}).get('mid')
        if not h or not cur:return None
        target=now_-seconds*1000; cand=None
        for ts,p in reversed(h):
            if ts<=target: cand=(ts,p);break
        return None if not cand or cand[1]<=0 else cur/cand[1]-1.0

    def start_ref(self,m):
        arr=self.chainlink.get(m['asset']) or []
        if not arr:return None
        near=min(arr,key=lambda x:abs(x[0]-m['event_start_ms']))
        return {'source_ts_ms':near[0],'price':near[1],'receive_ts_ms':near[2],'delta_ms':abs(near[0]-m['event_start_ms'])}

    async def sample_loop(self):
        last_book=last_bin=0
        while not self.stop.is_set():
            recv=now_ms()
            if recv-last_bin>=BINANCE_SAMPLE_MS:
                last_bin=recv
                for sym,s in list(self.binance.items()):
                    if not s.get('mid'):continue
                    depth=s.get('depth5') or {};vol=None
                    k=(s.get('kline') or {}).get('k') or {}
                    if k:vol=finite(k.get('v'))
                    lt=self.binance_trades.get(sym); tr=lt[-1] if lt else None
                    await self.emit({'kind':'binance_tick','key':key(RUN_ID,sym,'sample',recv//BINANCE_SAMPLE_MS),'run_id':RUN_ID,'source_ts_ms':tr[0] if tr else None,
                      'receive_ts_ms':recv,'exchange_ts_ms':tr[0] if tr else None,'source':'BINANCE_PUBLIC_MARKET_DATA','schema_version':SCHEMA_VERSION,'symbol':sym,'stream':'COMPOSITE_SAMPLE',
                      'best_bid':s.get('bid'),'best_bid_qty':s.get('bq'),'best_ask':s.get('ask'),'best_ask_qty':s.get('aq'),'mid':s.get('mid'),'microprice':s.get('microprice'),
                      'trade_price':tr[1] if tr else None,'trade_qty':tr[2] if tr else None,'trade_side':tr[3] if tr else None,'volume':vol,
                      'payload':{'bookTicker_receive_ts_ms':s.get('receive_ts_ms'),'depth5':depth,'kline':k,'exchange_timestamp_available_for_bookTicker':False,'aggTrade_exchange_ts_ms':tr[0] if tr else None}})
            if recv-last_book>=BOOK_SAMPLE_MS:
                last_book=recv
                for token,meta in list(self.token_meta.items()):
                    b=self.state_book_dict(token,10);state=self.books.get(token)
                    if not b or not state:continue
                    bm=book_metrics(b)
                    await self.emit({'kind':'poly_book','key':key(RUN_ID,token,recv//BOOK_SAMPLE_MS),'run_id':RUN_ID,'source_ts_ms':state['source_ts_ms'],'receive_ts_ms':recv,
                      'source':'POLYMARKET_CLOB_WS_STATE','schema_version':SCHEMA_VERSION,'asset':meta['asset'],'horizon_s':meta['horizon_s'],'market_id':meta['market_id'],
                      'condition_id':meta['condition_id'],'token_id':token,'outcome':meta['outcome'],'best_bid':bm['best_bid'],'best_ask':bm['best_ask'],
                      'bid_depth_5':bm['bid_depth_5'],'ask_depth_5':bm['ask_depth_5'],'book_hash':state.get('hash'),'bids':b['bids'],'asks':b['asks'],
                      'payload':{'full_sync_age_ms':recv-state.get('full_sync_ms',0),'state_source_ts_ms':state['source_ts_ms']}})
            await asyncio.sleep(.2)

    async def evaluate_market(self,m,recv):
        y=self.state_book_dict(m['token_yes'],50);n=self.state_book_dict(m['token_no'],50);ys=self.books.get(m['token_yes']);ns=self.books.get(m['token_no']);bs=self.binance.get(m['symbol']) or {}
        if not y or not n or not ys or not ns or not bs.get('mid'):return
        poly_age=recv-min(ys['receive_ts_ms'],ns['receive_ts_ms']); bin_age=recv-bs.get('receive_ts_ms',0)
        if poly_age>MAX_STALE_POLY_MS or bin_age>MAX_STALE_BINANCE_MS:return
        ym=book_metrics(y);nm=book_metrics(n)
        if ym['best_bid'] is None or ym['best_ask'] is None or nm['best_bid'] is None or nm['best_ask'] is None:return
        poly_mid=(ym['best_bid']+ym['best_ask'])/2
        h=self.poly_mid_hist.setdefault(m['condition_id'],collections.deque(maxlen=120)); prev=None
        target=recv-1000
        for ts0,p0 in reversed(h):
            if ts0<=target:prev=p0;break
        poly_change=None if prev is None else poly_mid-prev; h.append((recv,poly_mid)); self.counters['market_evaluations']+=1
        # Exact current-state join uses local receive-time alignment because Binance bookTicker has no exchange timestamp.
        delta=abs(max(ys['receive_ts_ms'],ns['receive_ts_ms'])-bs['receive_ts_ms'])
        timing='PASS_RECEIVE_TIME' if delta<=JOIN_TOLERANCE_MS else 'REJECT_TIMING_UNCERTAINTY'
        ref=self.start_ref(m)
        threshold_ok=bool(ref and ref['delta_ms']<=1500)
        ret1=self.hist_return(m['symbol'],recv,1);ret5=self.hist_return(m['symbol'],recv,5);ret30=self.hist_return(m['symbol'],recv,30);ret60=self.hist_return(m['symbol'],recv,60)
        # M0 baseline only during collection: current executable mid. Incremental model is fit strictly OOS in reports.
        fair=poly_mid; fv_delta=0.0
        jk=key(RUN_ID,m['condition_id'],'join',recv//EVAL_MS)
        await self.emit({'kind':'crossvenue_join','key':jk,'run_id':RUN_ID,'source_ts_ms':max(ys['source_ts_ms'],ns['source_ts_ms']),'receive_ts_ms':recv,'source':'POLYMARKET_X_BINANCE',
          'schema_version':SCHEMA_VERSION,'asset':m['asset'],'symbol':m['symbol'],'market_id':m['market_id'],'condition_id':m['condition_id'],'horizon_s':m['horizon_s'],
          'poly_implied_probability':poly_mid,'poly_price_change':poly_change,'binance_mid':bs['mid'],'binance_return_1s':ret1,'binance_return_5s':ret5,'binance_return_30s':ret30,'binance_return_60s':ret60,
          'fair_value_estimate':fair,'fair_value_delta_bps':fv_delta,'lead_lag_ms':delta,'book_response_latency_ms':None,'clock_offset_ms':self.clock_offset_ms,'network_latency_ms':self.network_latency_ms,
          'join_tolerance_ms':JOIN_TOLERANCE_MS,'timing_quality':timing if threshold_ok else 'REJECT_CHAINLINK_START_REFERENCE_MISSING',
          'payload':{'model':'M0_POLY_MID_BASELINE','chainlink_start_reference':ref,'chainlink_reference_status':'EXACT_NEAREST_1S' if threshold_ok else 'NOT_EXACT_ENOUGH',
          'binance_role':'CROSS_VENUE_PROXY_NOT_RESOLUTION_SOURCE','resolution_source':m['resolution_source'],'poly_age_ms':poly_age,'binance_age_ms':bin_age}})
        if threshold_ok:self.counters['chainlink_start_ref_exact']+=1
        else:self.counters['chainlink_start_ref_missing']+=1
        if timing!='PASS_RECEIVE_TIME':self.counters['timing_reject']+=1;return
        self.counters['timing_pass']+=1
        cap=pair_capacity(y,n,m['taker_fee_rate']);snap=pair_snapshot(y,n,m['taker_fee_rate'],10)
        if snap.get('net_edge_bps') is None:return
        opp={'kind':'opportunity','key':key(RUN_ID,m['condition_id'],'opp',recv//EVAL_MS),'run_id':RUN_ID,'source_ts_ms':max(ys['source_ts_ms'],ns['source_ts_ms']),'receive_ts_ms':recv,
          'source':'SENEX_ARBITRAGE_SHADOW_V1','schema_version':SCHEMA_VERSION,'asset':m['asset'],'horizon_s':m['horizon_s'],'market_id':m['market_id'],'condition_id':m['condition_id'],
          'token_yes':m['token_yes'],'token_no':m['token_no'],'yes_best_bid':snap['yes_best_bid'],'yes_best_ask':snap['yes_best_ask'],'no_best_bid':snap['no_best_bid'],'no_best_ask':snap['no_best_ask'],
          'yes_depth':snap['yes_depth'],'no_depth':snap['no_depth'],'gross_cost':snap['gross_cost'],'fee_cost':snap['fee_cost'],'slippage_cost':snap['slippage_cost'],'net_cost':snap['net_cost'],
          'net_edge_bps':snap['net_edge_bps'],'simulated_fill_yes':snap['yes']['filled'],'simulated_fill_no':snap['no']['filled'],'legging_failure':snap['legging_failure'],'time_between_legs_ms':0,
          'capacity_usd':cap['capacity_usd'],'payload':{'probe_shares':10,'positive_marginal_capacity':cap,'candidate':snap['net_edge_bps']>0}}
        # Store every positive candidate; store one negative falsification sample each 30 seconds per market.
        if snap['net_edge_bps']>0 or recv//30000 != (recv-EVAL_MS)//30000:
            await self.emit(opp); self.counters['opportunity_samples']+=1
        if snap['net_edge_bps']>0:
            self.counters['positive_candidate']+=1;await self.paper_execute(m,opp,recv)

    async def paper_execute(self,m,opp,detected_ms):
        # Both orderings are simulated independently. The first leg consumes t0 book, second leg uses a fresh t1 REST book after measured latency.
        for first in ['YES','NO']:
            try:
                y0,n0=await fetch_pair_books(self.session,m); start=now_ms(); bm0={'YES':y0,'NO':n0}; other='NO' if first=='YES' else 'YES'
                top_pair=(book_metrics(y0)['best_ask'] or 1)+(book_metrics(n0)['best_ask'] or 1)
                for capital in CAPITAL_LEVELS:
                    shares=max(0.0,capital/max(top_pair,1e-9)); f1=consume(book_metrics(bm0[first])['asks'],shares,m['taker_fee_rate'])
                    await asyncio.sleep(PAPER_LATENCY_MS/1000)
                    y1,n1=await fetch_pair_books(self.session,m); bm1={'YES':y1,'NO':n1}; f2=consume(book_metrics(bm1[other])['asks'],f1['filled'],m['taker_fee_rate'])
                    paired=min(f1['filled'],f2['filled']); residual=max(0.0,f1['filled']-paired)
                    payout=paired; cost1=(f1['notional']+f1['fee'])*(paired/f1['filled']) if f1['filled'] else 0; cost2=(f2['notional']+f2['fee'])*(paired/f2['filled']) if f2['filled'] else 0
                    pnl=payout-cost1-cost2
                    episode_key=key(opp['key'],first,capital,start)
                    payload={'capital_usd':capital,'first_leg':first,'second_leg':other,'paired_shares':paired,'residual_first_leg_shares':residual,'paired_pnl_usd':pnl,
                      'latency_ms':now_ms()-start,'first_fill':f1,'second_fill':f2,'optimistic_fill':False,'second_book_fetched_after_latency':True}
                    for leg,ff in [(first,f1),(other,f2)]:
                        self.counters['paper_fill_rows']+=1
                        await self.emit({'kind':'paper_fill','key':key(episode_key,leg),'opportunity_key':opp['key'],'run_id':RUN_ID,'source_ts_ms':detected_ms,'receive_ts_ms':now_ms(),
                          'source':'SENEX_PAPER_EXECUTION_V1','schema_version':SCHEMA_VERSION,'asset':m['asset'],'market_id':m['market_id'],'condition_id':m['condition_id'],'leg':leg,
                          'status':'FULL' if ff['residual']<=1e-9 else ('PARTIAL' if ff['filled']>0 else 'MISS'),'requested_shares':ff['requested'],'filled_shares':ff['filled'],
                          'vwap':ff['vwap'],'notional':ff['notional'],'fee':ff['fee'],'slippage':None,'latency_ms':payload['latency_ms'],'payload':payload})
                    self.log_raw('paper-execution',{'detected_ms':detected_ms,'opportunity_key':opp['key'],'episode_key':episode_key,'payload':payload})
            except Exception as e:self.counters['paper_execute_errors']+=1;self.last_errors.append({'at_ms':now_ms(),'where':'paper_execute','error':str(e)[:200]})

    async def evaluation_loop(self):
        while not self.stop.is_set():
            recv=now_ms()
            for m in list(self.markets):
                try:await self.evaluate_market(m,recv)
                except Exception as e:self.counters['evaluate_errors']+=1;self.last_errors.append({'at_ms':now_ms(),'where':'evaluate','error':str(e)[:200]})
            await asyncio.sleep(EVAL_MS/1000)

    async def metric_loop(self):
        while not self.stop.is_set():
            await asyncio.sleep(60)
            recv=now_ms(); metrics={**dict(self.counters),'elapsed_hours':(recv-self.started_ms)/3600000,'ingest_queue':self.ingest_q.qsize(),'chainlink_btc_buffer':len(self.chainlink['BTC']),'chainlink_eth_buffer':len(self.chainlink['ETH'])}
            for name,val in metrics.items():
                if isinstance(val,(int,float)):
                    await self.emit({'kind':'metric','key':key(RUN_ID,'runtime',name,recv//60000),'run_id':RUN_ID,'source_ts_ms':recv,'receive_ts_ms':recv,'source':'SENEX_ORDER075_RUNTIME','schema_version':SCHEMA_VERSION,'window_name':'RUNNING_1M','metric_name':name,'metric_value':float(val),'unit':'count_or_value','dimensions':{},'payload':{'counter_snapshot':True}})

    async def state_loop(self):
        while not self.stop.is_set():
            st={'run_id':RUN_ID,'started_ms':self.started_ms,'now_ms':now_ms(),'elapsed_hours':(now_ms()-self.started_ms)/3600000,'markets':[{k:v for k,v in m.items() if k!='raw'} for m in self.markets],
              'books':len(self.books),'binance_symbols':sorted(self.binance),'chainlink_counts':{k:len(v) for k,v in self.chainlink.items()},'counters':dict(self.counters),'queue':self.ingest_q.qsize(),'last_errors':list(self.last_errors)[-10:],
              'locks':{'paper':True,'live_capital_locked':True,'orders':False,'polymarket_order_placement':False,'binance_order_placement':False}}
            (STATE_DIR/'status.json').write_text(json.dumps(st,indent=2))
            await asyncio.sleep(10)

    async def main(self):
        timeout=aiohttp.ClientTimeout(total=20);self.session=aiohttp.ClientSession(headers={'User-Agent':UA},timeout=timeout)
        await self.run_row('RUNNING')
        tasks=[asyncio.create_task(x()) for x in [self.ingest_loop,self.discover_loop,self.poly_ws_loop,self.binance_loop,self.full_resync_loop,self.sample_loop,self.evaluation_loop,self.metric_loop,self.state_loop]]
        tasks += [asyncio.create_task(self.rtds_asset_loop(a)) for a in ['BTC','ETH']]
        try:await self.stop.wait()
        finally:
            await self.run_row('STOPPING');await asyncio.sleep(2)
            for t in tasks:
                if t.get_coro().__name__!='ingest_loop':t.cancel()
            await self.ingest_q.join();self.stop.set()
            for t in tasks:
                if not t.done():t.cancel()
            self.close_raw(); await self.session.close()

lab=Lab()
def halt(*_):lab.stop.set()
for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,halt)
asyncio.run(lab.main())
