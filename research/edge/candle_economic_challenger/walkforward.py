"""Frozen A/B/C/D BTC 1h PAPER reference study; chronological labels only."""
from __future__ import annotations
import math
from datetime import timedelta
import numpy as np
from .contracts import EvidenceError, utc
from .candle_features import GEOMETRY, PATTERNS
from .economic_labels import CostModel, economic_trade

MODELS={"A":(),"B":GEOMETRY,"C":PATTERNS,"D":GEOMETRY+PATTERNS}
GRID=(10,15,20,30)

def _validate(rows):
    seen=set(); last=None
    for r in rows:
        if not isinstance(r,dict) or not str(r.get("market_id") or ""): raise EvidenceError("missing identity")
        if r["market_id"] in seen: raise EvidenceError("duplicate market id")
        seen.add(r["market_id"])
        ts=utc(r.get("decision_ts")); end=utc(r.get("label_end_ts"))
        if end-ts!=timedelta(seconds=3600): raise EvidenceError("wrong label horizon")
        if last is not None and ts<=last: raise EvidenceError("unsorted T0")
        last=ts
        if r.get("side") not in ("LONG","SHORT","FLAT"): raise EvidenceError("baseline side")
        for group,keys in (("geometry",GEOMETRY),("patterns",PATTERNS)):
            f=r.get(group)
            if not isinstance(f,dict) or set(f)!=set(keys): raise EvidenceError("incomplete feature schema")
            for key in keys:
                value=f[key]
                if group=="patterns" and (type(value) not in (bool,int) or value not in (0,1)):
                    raise EvidenceError("invalid pattern flag")
                if group=="geometry" and (type(value) not in (int,float) or not math.isfinite(value)):
                    raise EvidenceError("invalid geometry")
        for key in ("entry_reference","exit_reference"):
            try: p=float(r[key])
            except (KeyError,TypeError,ValueError) as exc: raise EvidenceError("missing reference price") from exc
            if not math.isfinite(p) or p<=0: raise EvidenceError("invalid reference price")

def purged_splits(rows,n_splits=3,min_train=30,embargo_seconds=3600):
    if n_splits<1 or min_train<4 or embargo_seconds<3600: raise EvidenceError("unsafe CV config")
    _validate(rows)
    first=None
    for i in range(min_train,len(rows)):
        cutoff=utc(rows[i]["decision_ts"])-timedelta(seconds=embargo_seconds)
        train=[j for j in range(i) if utc(rows[j]["label_end_ts"])<cutoff]
        if len(train)>=min_train: first=i;break
    if first is None: raise EvidenceError("not enough purged history")
    groups=np.array_split(np.arange(first,len(rows)),min(n_splits,len(rows)-first))
    folds=[]
    for group in groups:
        test=tuple(int(x) for x in group)
        cutoff=utc(rows[test[0]]["decision_ts"])-timedelta(seconds=embargo_seconds)
        train=tuple(j for j in range(test[0]) if utc(rows[j]["label_end_ts"])<cutoff)
        if len(train)<min_train or set(train)&set(test): raise EvidenceError("fold overlap")
        folds.append((train,test))
    return folds

def _matrix(rows,keys):
    return np.asarray([[float((r["geometry"] if k in GEOMETRY else r["patterns"])[k]) for k in keys] for r in rows],dtype=float)

def _training_only_predict(train,test,keys,cost):
    x=_matrix(train,keys); z=_matrix(test,keys)
    y=np.asarray([economic_trade(r["side"],r["entry_reference"],r["exit_reference"],cost,3600)["net_bps"] for r in train])
    mean=x.mean(axis=0); std=np.where(x.std(axis=0)>1e-12,x.std(axis=0),1.)
    x=(x-mean)/std; z=(z-mean)/std
    design=np.column_stack((np.ones(len(x)),x))
    ridge=np.eye(design.shape[1])*10.;ridge[0,0]=0.
    beta=np.linalg.solve(design.T@design+ridge,design.T@y)
    return (np.column_stack((np.ones(len(z)),z))@beta).tolist()

def _summary(values,sides):
    x=np.asarray(values,dtype=float);curve=np.r_[0.,np.cumsum(x)]
    wins=float(x[x>0].sum());loss=abs(float(x[x<0].sum()))
    return {"n_opportunities":len(x),"n_trades":sum(s!="FLAT" for s in sides),
        "long_trades":sides.count("LONG"),"short_trades":sides.count("SHORT"),
        "net_mean_bps":float(x.mean()),"net_sum_bps":float(x.sum()),
        "max_drawdown_bps":float(np.max(np.maximum.accumulate(curve)-curve)),
        "profit_factor":wins/loss if loss else None}

def _paired(delta,seed,n):
    delta=np.asarray(delta,dtype=float);size=len(delta);rng=np.random.default_rng(seed)
    values=[];block=min(24,size)
    for _ in range(n):
        starts=rng.integers(0,size,size=math.ceil(size/block))
        ids=np.concatenate([(np.arange(s,s+block)%size) for s in starts])[:size]
        values.append(float(delta[ids].mean()))
    p=(1+sum(x<=0 for x in values))/(n+1)
    return {"mean_net_bps":float(delta.mean()),
        "ci95":[float(np.quantile(values,.025)),float(np.quantile(values,.975))],
        "one_sided_p":p,"holm_conservative_significance":p<.05/3}

def evaluate_abcd(rows,*,min_train=30,n_splits=3,n_bootstrap=1000,seed=7,custody_verified=False):
    if not rows:
        return {"verdict":"BLOCKED_ARTIFACT_BYTES","reason":"NO_CAUSAL_T0_SOURCE_BYTES",
                "scenarios":{},"n_oos":0,"experimental_only":True}
    if n_bootstrap<50 or type(seed)!=int: raise EvidenceError("invalid bootstrap contract")
    folds=purged_splits(rows,n_splits=n_splits,min_train=min_train)
    indices=[i for _,test in folds for i in test]
    if len(indices)!=len(set(indices)): raise EvidenceError("test reuse")
    scenarios={}
    for total in GRID:
        cost=CostModel(total*2/3,0.,total/3,0.,0.)
        outcome={key:[] for key in MODELS};actions={key:[] for key in MODELS}
        for train,test in folds:
            tr=[rows[i] for i in train];te=[rows[i] for i in test]
            for name,keys in MODELS.items():
                scores=[float("inf")]*len(te) if name=="A" else _training_only_predict(tr,te,keys,cost)
                for row,score in zip(te,scores):
                    side=row["side"] if score>0 else "FLAT"
                    trade=economic_trade(side,row["entry_reference"],row["exit_reference"],cost,3600)
                    outcome[name].append(trade["net_bps"]);actions[name].append(side)
        result={key:_summary(outcome[key],actions[key]) for key in MODELS}
        for j,name in enumerate(("B","C","D")):
            result["paired_delta_"+name]=_paired(np.asarray(outcome[name])-np.asarray(outcome["A"]),seed+j,n_bootstrap)
        scenarios[str(total)]=result
    n=len(indices)
    if n<300: verdict="INSUFFICIENT_EVIDENCE"
    elif not custody_verified: verdict="BLOCKED_ARTIFACT_BYTES"
    else:
        candidate=any(all(scenarios[c][f"paired_delta_{m}"]["ci95"][0]>0 and
                scenarios[c][m]["net_mean_bps"]>0 and
                scenarios[c][m]["n_trades"]>=100 and
                scenarios[c][f"paired_delta_{m}"]["holm_conservative_significance"]
                for c in ("15","20","30")) for m in ("B","C","D"))
        verdict="HISTORICAL_OOS_CANDIDATE_ONLY" if candidate else "EDGE_NOT_DEMONSTRATED"
    return {"verdict":verdict,"n_oos":n,"n_folds":len(folds),
        "fold_bounds":[{"train_end":rows[tr[-1]]["decision_ts"],"test_start":rows[te[0]]["decision_ts"],
        "test_end":rows[te[-1]]["decision_ts"],"n_train":len(tr),"n_test":len(te)} for tr,te in folds],
        "scenarios":scenarios,"experimental_only":True,
        "cost_bps_round_trip":list(GRID),"bootstrap_replicates":n_bootstrap,
        "bootstrap_block_observations":24,"seed":seed,"source_custody_verified":bool(custody_verified)}
