"""M17-P2 offline-only parser with exact original-byte, real local custody.

No network, authentication, signed-oracle assertion or market orders. Derived
views are intentionally untrusted relative to the original provider bytes.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path


class EvidenceError(ValueError):
    """Malformed/inconsistent original source or custody; stop, never repair."""


def _millis(v):
    if type(v) is not int or v < 0:
        raise EvidenceError("noncanonical event/receive wall timestamp")
    return v


def _decimal(v):
    if not isinstance(v, str):
        raise EvidenceError("exact decimal must be source string")
    try:
        d = Decimal(v)
    except InvalidOperation as exc:
        raise EvidenceError("malformed source decimal") from exc
    if not d.is_finite() or d <= 0:
        raise EvidenceError("nonpositive/nonfinite source decimal")
    return d


def _identity(doc, *, condition_id, asset_id):
    if doc.get('market') != condition_id or doc.get('asset_id') != asset_id:
        raise EvidenceError("wrong CLOB market/asset identity")


def _normalize(doc, kind, *, market_id, condition_id, asset_id):
    if kind == 'GAMMA':
        if str(doc.get('id')) != market_id or doc.get('conditionId') != condition_id:
            raise EvidenceError("Gamma identity not proven from original bytes")
        try:
            tokens = json.loads(doc['clobTokenIds']) if isinstance(doc['clobTokenIds'],str) else doc['clobTokenIds']
            outcomes = json.loads(doc['outcomes']) if isinstance(doc['outcomes'],str) else doc['outcomes']
        except (KeyError,TypeError,ValueError) as exc:
            raise EvidenceError("Gamma outcome/token mapping ambiguous") from exc
        if not (isinstance(tokens,list) and len(tokens)==2 and len(set(tokens))==2 and
                isinstance(outcomes,list) and len(outcomes)==2 and asset_id in tokens):
            raise EvidenceError("Gamma outcome/token identity ambiguous")
        return 'METADATA_UNVERIFIED_RULE', {'token_ids':tokens,'outcomes':outcomes,
                 'settlement_rule_verified':None,'rule_regime':'UNKNOWN','asset_protocol':'UNVERIFIED'} , None, None
    if kind == 'CLOB_REST':
        _identity(doc,condition_id=condition_id,asset_id=asset_id)
        event=_millis(doc.get('timestamp'))
        if not (isinstance(doc.get('hash'),str) and doc['hash'] and
                isinstance(doc.get('bids'),list) and isinstance(doc.get('asks'),list)):
            raise EvidenceError("missing original book hash/levels")
        for key in ('min_order_size','tick_size'):
            _decimal(doc.get(key))
        from .quote_readiness import QuoteError, _validate_levels
        try:
            _validate_levels(doc['asks'],doc['bids'],_decimal(doc['tick_size']))
        except QuoteError as exc:
            raise EvidenceError('invalid original CLOB levels') from exc
        return 'BOOK_QUOTE_ONLY', {'market':condition_id,'asset_id':asset_id,
               'hash':doc['hash'],'min_order_size':doc['min_order_size'],
               'tick_size':doc['tick_size'],'asks':doc['asks'],'bids':doc['bids']}, event, None
    if kind == 'RTDS_LEGACY':
        if doc.get('topic') != 'crypto_prices_twap_sixty':
            raise EvidenceError("legacy spot/generic Chainlink is not TWAP60")
        payload=doc.get('payload')
        if not isinstance(payload,dict) or payload.get('symbol')!='btc/usd' or payload.get('window_s')!=60:
            raise EvidenceError("legacy TWAP60 payload invalid")
        envelope=_millis(doc.get('timestamp'))
        event=_millis(payload.get('timestamp', envelope))
        if doc.get('type')!='update':
            return 'SNAPSHOT_NOT_FORWARD', {'symbol':'btc/usd','window_seconds':60},event,None
        original=payload.get('full_accuracy_value')
        if not isinstance(original,str) or not re.fullmatch(r'[0-9]+',original):
            raise EvidenceError("legacy E18 must be integer string")
        value=Decimal(original).scaleb(-18)
        return 'OFFLINE_UPDATE_UNVERIFIED',{'symbol':'btc/usd','window_seconds':60,
            'value_decimal':format(value,'f'),'scale':'LEGACY_E18'},event,None
    if kind == 'POLYBOLT_RAW':
        if doc.get('v')!=1 or type(doc.get('seq')) is not int or doc['seq']<0:
            raise EvidenceError("unsupported PolyBolt version/sequence")
        if doc.get('channel')!='price.crypto.twap':
            raise EvidenceError("PolyBolt spot channel is not TWAP60")
        payload=doc.get('payload')
        if not isinstance(payload,dict) or payload.get('symbol')!='btcusd' or payload.get('window_seconds')!=60:
            raise EvidenceError("PolyBolt raw source filter invalid")
        event=_millis(doc.get('ts'))
        if doc.get('dropped',0)!=0 or type(doc.get('snapshot',False)) is not bool:
            raise EvidenceError("PolyBolt wire gap/invalid snapshot flag")
        if doc.get('snapshot',False):
            return 'SNAPSHOT_NOT_FORWARD',{'symbol':'btcusd','window_seconds':60},event,doc['seq']
        value=_decimal(payload.get('full_accuracy_value'))
        return 'OFFLINE_UPDATE_UNVERIFIED',{'symbol':'btcusd','window_seconds':60,
                   'value_decimal':str(value),'scale':'DECIMAL_NO_E18'},event,doc['seq']
    if kind == 'SDK_NORMALIZED':
        if doc.get('topic')!='prices.crypto.twap' or doc.get('type') not in ('subscribe','update'):
            raise EvidenceError("SDK spot/symbol/type not TWAP60")
        payload=doc.get('payload')
        if not isinstance(payload,dict) or payload.get('symbol')!='btcusd' or payload.get('windowSeconds')!=60:
            raise EvidenceError("SDK TWAP60 identity invalid")
        event=_millis(doc.get('timestamp'))
        if doc['type']=='subscribe':
            return 'SNAPSHOT_NOT_FORWARD',{'symbol':'btcusd','window_seconds':60},event,None
        value=_decimal(payload.get('value'))
        return 'SDK_DERIVED_UPDATE_UNVERIFIED',{'symbol':'btcusd','window_seconds':60,
                 'value_decimal':str(value),'scale':'DECIMAL_NO_E18'},_millis(payload.get('timestamp')),None
    raise EvidenceError("unknown provider source class")


class OriginalOfflineCustody:
    """Exclusive local raw blobs + digest-chained journal; verifies on restart.

    A user able to rewrite an entire directory can recompute every local hash;
    without an off-host anchor this is NOT externally immutable custody.
    """
    def __init__(self,path):
        self.path=Path(path)
        self.path.mkdir(parents=True,exist_ok=True)
        if self.path.is_symlink():
            raise EvidenceError('symlink custody directory')
        self.reopen()

    def reopen(self):
        journal=self.path/'journal.jsonl'
        records=[]
        prev='0'*64
        seen=set()
        if journal.is_symlink(): raise EvidenceError('symlink journal')
        if journal.exists():
            raw=journal.read_bytes()
            if raw and not raw.endswith(b'\n'):
                raise EvidenceError('torn journal tail')
            for line in raw.splitlines():
                try:
                    r=json.loads(line)
                    expected=hashlib.sha256(json.dumps({k:v for k,v in r.items() if k!='chain_sha256'},sort_keys=True,separators=(',',':')).encode()).hexdigest()
                    if r['chain_sha256']!=expected or r['previous_sha256']!=prev:
                        raise EvidenceError('journal chain tamper')
                    fn=r['original_attachment']
                    if not re.fullmatch(r'raw_[0-9]{6}_[0-9a-f]{64}\.bin',fn):
                        raise EvidenceError('bad blob path')
                    bp=self.path/fn
                    if bp.is_symlink() or not bp.is_file():raise EvidenceError('lost/symlink raw blob')
                    data=bp.read_bytes()
                    if len(data)!=r['original_length'] or hashlib.sha256(data).hexdigest()!=r['original_sha256']:
                        raise EvidenceError('tampered original blob')
                    if fn in seen or r['journal_sequence']!=len(records)+1:raise EvidenceError('duplicate/broken journal sequence')
                    seen.add(fn)
                    prev=expected
                    records.append(r)
                except (ValueError,KeyError,TypeError,UnicodeDecodeError) as exc:
                    raise EvidenceError('invalid original journal') from exc
        disk={p.name for p in self.path.iterdir() if p.name!='journal.jsonl'}
        if disk!=seen:
            raise EvidenceError('crash orphan/extraneous path; manual adjudication')
        return records

    def record(self,raw,*,source_class,parser_version,received_wall_ms,
               received_monotonic_ns,market_id,condition_id,asset_id,connection_id,
               source_admissible=False):
        if source_admissible is not False:
            raise EvidenceError('caller cannot self-attest source authority')
        if type(raw) is not bytes or not raw or len(raw)>65536:
            raise EvidenceError('missing/oversized original bytes')
        _millis(received_wall_ms)
        if type(received_monotonic_ns) is not int or received_monotonic_ns<0:
            raise EvidenceError('noncanonical monotonic receipt clock')
        if any(not isinstance(v,str) or not v for v in
               (source_class,parser_version,market_id,condition_id,asset_id,connection_id)):
            raise EvidenceError('incomplete source identity')
        try:
            doc=json.loads(raw,parse_float=Decimal,parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (UnicodeDecodeError,ValueError,TypeError) as exc:
            raise EvidenceError('invalid source JSON') from exc
        if not isinstance(doc,dict):raise EvidenceError('source object required')
        status,normalized,event,seq=_normalize(doc,source_class,market_id=market_id,
                             condition_id=condition_id,asset_id=asset_id)
        if event is not None and event>received_wall_ms:
            raise EvidenceError('source event in future relative to wall clock')
        prior=self.reopen()
        if source_class=='POLYBOLT_RAW':
            matches=[r for r in prior if r['source_class']==source_class and r['connection_id']==connection_id and r['channel']=='price.crypto.twap']
            if matches and seq!=matches[-1]['source_sequence']+1:
                raise EvidenceError('GAP: sequence not dense per connection/channel')
            if not matches and not doc.get('snapshot',False):
                raise EvidenceError('GAP: no channel snapshot/initial seq prior')
        sha=hashlib.sha256(raw).hexdigest()
        filename=f'raw_{len(prior)+1:06d}_{sha}.bin'
        entry={
          'journal_sequence':len(prior)+1,'previous_sha256':prior[-1]['chain_sha256'] if prior else '0'*64,
          'original_sha256':sha,'original_length':len(raw),'original_attachment':filename,
          'source_class':source_class,'source_origin':'SDK_DERIVED_NOT_ORIGINAL_WIRE' if source_class=='SDK_NORMALIZED' else 'PROVIDED_BYTES_UNATTESTED',
          'parser_version':parser_version,'market_id':market_id,'condition_id':condition_id,
          'asset_id':asset_id,'connection_id':connection_id,'channel':doc.get('channel'),
          'source_sequence':seq,'source_event_wall_ms':event,'received_wall_ms':received_wall_ms,
          'received_monotonic_ns':received_monotonic_ns,'status':status,
          'normalized':normalized,'source_admissible':False,'label_authority':'UNVERIFIED',
          'eligible':False,'fill_proven':False,'fixture_class':'DOCUMENTATION_FIXTURE',
        }
        entry['chain_sha256']=hashlib.sha256(json.dumps(entry,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        # No concurrent writer support. Exclusive create + orphan detection fails closed.
        bp=self.path/filename
        try:
            with bp.open('xb') as f:
                f.write(raw);f.flush();os.fsync(f.fileno())
            with (self.path/'journal.jsonl').open('ab') as f:
                f.write(json.dumps(entry,sort_keys=True,separators=(',',':')).encode()+b'\n');f.flush();os.fsync(f.fileno())
        except OSError as exc:
            raise EvidenceError('custody append incomplete; do not auto-repair') from exc
        return entry
