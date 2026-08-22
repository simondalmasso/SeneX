from __future__ import annotations
import datetime as dt, json, os, re, statistics, urllib.error, urllib.parse, urllib.request
from pathlib import Path

API='https://api.northflank.com/v1'; PROJECT='seneciobot'; SERVICE='senecio-h011'
ORIGIN='https://h011-web--senecio-h011--wbjggn89fnf8.code.run'
TOKEN=os.environ['NORTHFLANK_API_TOKEN']; OUT=Path('order070-r8-forensic-evidence'); OUT.mkdir(exist_ok=True)
H={'Authorization':f'Bearer {TOKEN}','Accept':'application/json','User-Agent':'senex-order070-r8-forensic/1'}

def now(): return dt.datetime.now(dt.timezone.utc)
def req(url,headers=None,timeout=45):
    q=urllib.request.Request(url,headers=headers or {'Accept':'application/json','Cache-Control':'no-cache','User-Agent':'senex-order070-r8-public/1'},method='GET')
    try:
        with urllib.request.urlopen(q,timeout=timeout) as r: st=r.status; raw=r.read()
    except urllib.error.HTTPError as e: st=e.code; raw=e.read()
    except Exception as e: return {'http':0,'body':{'error':type(e).__name__}}
    try: body=json.loads(raw.decode())
    except Exception: body={'raw_sha256':__import__('hashlib').sha256(raw).hexdigest(),'bytes':len(raw)}
    return {'http':st,'body':body}
def nf(path,params=None):
    u=API+path
    if params: u+='?'+urllib.parse.urlencode(params,doseq=True)
    r=req(u,H,90)
    return r
def pub(path): return req(ORIGIN+path)

def parse_exec():
    p=OUT/'container_exec_samples.json'
    if not p.exists(): return {'available':False,'reason':'NO_EXEC_FILE'}
    doc=json.loads(p.read_text()); rows=[]
    for s in doc.get('samples',[]):
        text=s.get('stdout') or ''
        def one(pat, conv=float):
            m=re.search(pat,text,re.M)
            try:return conv(m.group(1)) if m else None
            except Exception:return None
        cur=one(r'^CGROUP_CURRENT=(\d+)$',int); mx=one(r'^CGROUP_MAX=(\d+)$',int)
        anon=one(r'^anon (\d+)$',int); file=one(r'^file (\d+)$',int); kernel=one(r'^kernel (\d+)$',int)
        rss=one(r'^VmRSS:\s+(\d+) kB$',int); ra=one(r'^RssAnon:\s+(\d+) kB$',int); rf=one(r'^RssFile:\s+(\d+) kB$',int)
        rows.append({'sample':s.get('sample'),'started':s.get('started'),'status':s.get('status'),'exitCode':s.get('exitCode'),'error':s.get('error'),
                     'cgroup_current_mb':cur/1048576 if cur else None,'cgroup_max_mb':mx/1048576 if mx else None,
                     'cgroup_anon_mb':anon/1048576 if anon is not None else None,'cgroup_file_mb':file/1048576 if file is not None else None,'cgroup_kernel_mb':kernel/1048576 if kernel is not None else None,
                     'uvicorn_rss_mb':rss/1024 if rss is not None else None,'uvicorn_rss_anon_mb':ra/1024 if ra is not None else None,'uvicorn_rss_file_mb':rf/1024 if rf is not None else None})
    good=[x for x in rows if x['cgroup_current_mb'] is not None]
    return {'available':bool(good),'rows':rows,'summary':{
        'cgroup_current_mb_max':max((x['cgroup_current_mb'] for x in good),default=None),
        'cgroup_anon_mb_max':max((x['cgroup_anon_mb'] for x in good if x['cgroup_anon_mb'] is not None),default=None),
        'cgroup_file_mb_max':max((x['cgroup_file_mb'] for x in good if x['cgroup_file_mb'] is not None),default=None),
        'uvicorn_rss_mb_max':max((x['uvicorn_rss_mb'] for x in good if x['uvicorn_rss_mb'] is not None),default=None),
        'uvicorn_rss_anon_mb_max':max((x['uvicorn_rss_anon_mb'] for x in good if x['uvicorn_rss_anon_mb'] is not None),default=None),
        'uvicorn_rss_file_mb_max':max((x['uvicorn_rss_file_mb'] for x in good if x['uvicorn_rss_file_mb'] is not None),default=None),
    }}

exec_summary=parse_exec(); end=now(); start=end-dt.timedelta(minutes=8)
metrics=nf(f'/projects/{PROJECT}/services/{SERVICE}/metrics',[
    ('queryType','range'),('startTime',start.isoformat().replace('+00:00','Z')),('endTime',end.isoformat().replace('+00:00','Z')),
    ('metricTypes','memory'),('metricTypes','cpu'),('metricTypes','requests'),('metricTypes','http5xxResponses'),('metricTypes','tcpConnectionsOpen')])
logs=nf(f'/projects/{PROJECT}/services/{SERVICE}/logs',[
    ('type','runtime'),('queryType','range'),('startTime',start.isoformat().replace('+00:00','Z')),('endTime',end.isoformat().replace('+00:00','Z')),('lineLimit',1000),('direction','forward')])
containers=nf(f'/projects/{PROJECT}/services/{SERVICE}/containers',[('per_page',100)])
public={
 'health':pub('/healthz'),'ready':pub('/readyz?symbol=BTCUSDT'),'provenance':pub('/api/runtime/provenance'),
 'state':pub('/api/oracle/state?symbol=BTCUSDT'),'snapshot':pub('/api/authority/snapshot?symbol=BTCUSDT'),
 'predictions':pub('/api/oracle/predictions/db?limit=5&symbol=BTCUSDT')}

doc={'captured_at':end.isoformat().replace('+00:00','Z'),'window_start':start.isoformat().replace('+00:00','Z'),'exec':exec_summary,'metrics':metrics,'logs':logs,'containers':containers,'public':public}
(OUT/'forensic_correlation.json').write_text(json.dumps(doc,indent=2,sort_keys=True,default=str)+'\n')
print('R8_FORENSIC_EXEC_AVAILABLE='+str(exec_summary.get('available')).upper())
for k,v in (exec_summary.get('summary') or {}).items(): print(f'{k.upper()}={v}')
print('NF_METRICS_HTTP='+str(metrics.get('http')))
print('NF_LOGS_HTTP='+str(logs.get('http')))
print('NF_CONTAINERS_HTTP='+str(containers.get('http')))
for k,r in public.items(): print(f'PUBLIC_{k.upper()}_HTTP={r.get("http")}')
# Print only runtime log lines relevant to memory/cycles/exits, capped.
rows=(logs.get('body') or {}).get('data',[]) if isinstance(logs.get('body'),dict) else []
if not rows and isinstance(logs.get('body'),list): rows=logs.get('body')
interesting=[]
for row in rows or []:
    text=str(row.get('log','')) if isinstance(row,dict) else str(row)
    if re.search(r'cycle|prediction|uvicorn|memory|killed|exit|terminated|authority|supabase',text,re.I):
        interesting.append({'ts':row.get('ts') if isinstance(row,dict) else None,'log':text[:500]})
print('INTERESTING_RUNTIME_LOGS='+json.dumps(interesting[-80:],separators=(',',':')))
