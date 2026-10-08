"""ORDER197 audit-corrected PAPER-only BTC 1h economic evaluation.

Identical frozen 15bps actions replay at all stress costs. Only verified
original bytes+AUD-trusted anchors can confer historical evidence status.
"""
from __future__ import annotations
import math
from datetime import timedelta
import numpy as np
from .contracts import EvidenceError, utc
from .candle_features import GEOMETRY, PATTERNS
from .economic_labels import CostModel, economic_trade
from .custody import VerifiedCustodyAttestation, verify_exact_byte_attestation

MODELS={"A":(),"E":(),"B":GEOMETRY,"C":PATTERNS,"D":GEOMETRY+PATTERNS}
GRID=(10,15,20,30)
TRAINING_COST_BPS=15
# 7 contrasts (B/C/D vs A + B/C/D vs E + D vs B) and 5 absolute nets.
FAMILY_ENDPOINTS=12
CONTRASTS={"paired_delta_B":("B","A"),"paired_delta_C":("C","A"),
    "paired_delta_D":("D","A"),"paired_delta_B_vs_E":("B","E"),
    "paired_delta_C_vs_E":("C","E"),"paired_delta_D_vs_E":("D","E"),
    "paired_delta_D_vs_B":("D","B")}

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
        "profit_factor":wins/loss if loss else None,
        "oos_actions":list(sides)}

def _interval(values,seed,n,*,block=24,family=FAMILY_ENDPOINTS):
    """Paired moving-block percentile CI, Bonferroni familywise adjustment.

    Uncentered bootstrap percentile CI is NOT a null-based p-value. No
    pseudo-pvalues are computed or reported.
    """
    x=np.asarray(values,dtype=float);size=len(x);rng=np.random.default_rng(seed)
    if size<1 or n<50 or family<1: raise EvidenceError("invalid bootstrap interval config")
    block=min(int(block),size)
    if block<1: raise EvidenceError("nonpositive bootstrap block")
    draws=[]
    for _ in range(n):
        starts=rng.integers(0,size,size=math.ceil(size/block))
        ids=np.concatenate([(np.arange(s,s+block)%size) for s in starts])[:size]
        draws.append(float(x[ids].mean()))
    alpha=.05/family
    return {"mean_net_bps":float(x.mean()),
        "familywise_ci_adjusted":[float(np.quantile(draws,alpha/2)),
            float(np.quantile(draws,1-alpha/2))],
        "method":"MOVING_BLOCK_PERCENTILE_BONFERRONI",
        "block_observations":block,"family_endpoints":family,
        "nominal_familywise_error":.05}

def promotion_gate(*,absolute_low,incremental_low,vs_no_candle_low,
                   trades,opportunities,independent_clusters=None,
                   patterns_incremental_low=None):
    if independent_clusters is None or independent_clusters<300:
        return False
    if trades<100 or opportunities<300: return False
    if not (absolute_low>0 and incremental_low>0 and vs_no_candle_low>0): return False
    return patterns_incremental_low is None or patterns_incremental_low>0

def _dependence(rows,indices,net_base):
    timestamps=[utc(rows[i]["decision_ts"]) for i in indices]
    clusters={t.replace(minute=0,second=0,microsecond=0) for t in timestamps}
    nonoverlap=0
    last_label_end=None
    for idx,t in zip(indices,timestamps):
        # Greedily count only disjoint true [T0, label_end) horizons.
        # Distinct hour-bucket names ALONE do not imply independence.
        if last_label_end is None or t>=last_label_end:
            nonoverlap+=1
            last_label_end=utc(rows[idx]["label_end_ts"])
    x=np.asarray(net_base,dtype=float)
    if len(x)<3 or x.std()<1e-12:
        neff=nonoverlap
    else:
        centered=x-x.mean();den=float(centered@centered)
        rho=[max(0.,float(centered[:-k]@centered[k:])/den)
              for k in range(1,min(24,len(x)-1)+1)]
        neff=max(1,min(nonoverlap,int(len(x)/(1+2*sum(rho)))))
    return {"nominal_unique_t0":len(indices),"independent_hour_clusters":len(clusters),
            "nonoverlapping_label_windows":nonoverlap,
            "estimated_effective_n_nonnegative_acf":neff,
            "effective_n_method":"CONSERVATIVE_POSITIVE_ACF_UP_TO_24; DIAGNOSTIC_ONLY"}
def evaluate_abcd(rows,*,min_train=30,n_splits=3,n_bootstrap=1000,seed=7,
                  custody_verified=False,attestation=None,source_bytes=None,label_bytes=None):
    if custody_verified:
        raise EvidenceError("custody caller assertion is prohibited; audited exact-byte attestation required")
    if not rows:
        return {"verdict":"BLOCKED_ARTIFACT_BYTES","scientific_verdict":"BLOCKED_ARTIFACT_BYTES",
            "reason":"NO_CAUSAL_T0_SOURCE_BYTES","scenarios":{},"n_oos":0,"experimental_only":True}
    if n_bootstrap<50 or type(seed)!=int: raise EvidenceError("invalid bootstrap contract")
    folds=purged_splits(rows,n_splits=n_splits,min_train=min_train)
    indices=[i for _,test in folds for i in test]
    if len(indices)!=len(set(indices)): raise EvidenceError("test reuse")
    training_cost=CostModel(10.,0.,5.,0.,0.)
    # CRITICAL: fit each model exactly once per fold on 15bps training rows.
    # Cost stress is later applied to THESE EXACT SAME FROZEN ACTIONS.
    policy={k:[] for k in MODELS}
    for train,test in folds:
        tr=[rows[i] for i in train];te=[rows[i] for i in test]
        mean_train_net=float(np.mean([economic_trade(r["side"],r["entry_reference"],
                             r["exit_reference"],training_cost,3600)["net_bps"] for r in tr]))
        predictions={"E":[mean_train_net]*len(te)}
        for name in ("B","C","D"):
            predictions[name]=_training_only_predict(tr,te,MODELS[name],training_cost)
        for row_i,row in enumerate(te):
            policy["A"].append(row["side"])
            for model in ("E","B","C","D"):
                policy[model].append(row["side"] if predictions[model][row_i]>0 else "FLAT")
    scenarios={}
    for total in GRID:
        # Same fees/slip proportions; only amount varies.
        cost=CostModel(total*2/3,0.,total/3,0.,0.)
        values={name:[economic_trade(side,rows[idx]["entry_reference"],
                     rows[idx]["exit_reference"],cost,3600)["net_bps"]
                     for idx,side in zip(indices,actions)]
                     for name,actions in policy.items()}
        scenario={name:_summary(values[name],policy[name]) for name in MODELS}
        for j,(field,(candidate,ref)) in enumerate(CONTRASTS.items()):
            scenario[field]=_interval(np.asarray(values[candidate])-np.asarray(values[ref]),
                                      seed+j,n_bootstrap)
        for j,name in enumerate(MODELS):
            result=_interval(values[name],seed+50+j,n_bootstrap)
            scenario[name]["absolute_net_ci_adjusted"]=result["familywise_ci_adjusted"]
        scenarios[str(total)]=scenario
    dep=_dependence(rows,indices,[economic_trade(policy["A"][j],rows[idx]["entry_reference"],
          rows[idx]["exit_reference"],training_cost,3600)["net_bps"]
          for j,idx in enumerate(indices)])
    anchor=None
    if (isinstance(attestation,VerifiedCustodyAttestation) and
            source_bytes is not None and label_bytes is not None):
        fresh=verify_exact_byte_attestation(rows,source_bytes=source_bytes,label_bytes=label_bytes)
        if fresh is not None and fresh==attestation: anchor=fresh
    n=len(indices)
    if anchor is None: scientific_verdict="BLOCKED_ARTIFACT_BYTES"
    elif n<300 or dep["estimated_effective_n_nonnegative_acf"]<300:
        scientific_verdict="INSUFFICIENT_EVIDENCE"
    else:
        candidate=False
        for model in ("B","C","D"):
            ok=all(promotion_gate(
                absolute_low=scenarios[str(cost)][model]["absolute_net_ci_adjusted"][0],
                incremental_low=scenarios[str(cost)]["paired_delta_"+model]["familywise_ci_adjusted"][0],
                vs_no_candle_low=scenarios[str(cost)]["paired_delta_"+model+"_vs_E"]["familywise_ci_adjusted"][0],
                patterns_incremental_low=(scenarios[str(cost)]["paired_delta_D_vs_B"]["familywise_ci_adjusted"][0]
                                           if model=="D" else None),
                trades=scenarios[str(cost)][model]["n_trades"],
                opportunities=n,independent_clusters=dep["estimated_effective_n_nonnegative_acf"])
                for cost in (15,20,30))
            if ok: candidate=True
        scientific_verdict="HISTORICAL_OOS_CANDIDATE_ONLY" if candidate else "EDGE_NOT_DEMONSTRATED"
    # Legacy small-sample status is descriptive of the synthetic fixture only.
    verdict="INSUFFICIENT_EVIDENCE" if n<300 else scientific_verdict
    sensitivity={str(block):_interval(
        np.asarray([economic_trade(s,rows[i]["entry_reference"],rows[i]["exit_reference"],
                 training_cost,3600)["net_bps"] for i,s in zip(indices,policy["D"])])-
        np.asarray([economic_trade(s,rows[i]["entry_reference"],rows[i]["exit_reference"],
                 training_cost,3600)["net_bps"] for i,s in zip(indices,policy["E"])]),
            seed,n_bootstrap,block=block)["familywise_ci_adjusted"] for block in (4,8,24,48)}
    return {"verdict":verdict,"scientific_verdict":scientific_verdict,
        "n_oos":n,"n_folds":len(folds),"family_comparisons":FAMILY_ENDPOINTS,
        "training_cost_bps":TRAINING_COST_BPS,
        "fold_bounds":[{"train_end":rows[tr[-1]]["decision_ts"],
            "test_start":rows[te[0]]["decision_ts"],"test_end":rows[te[-1]]["decision_ts"],
            "n_train":len(tr),"n_test":len(te)} for tr,te in folds],
        "dependence":dep,"block_length_sensitivity_D_vs_E":sensitivity,
        "scenarios":scenarios,"experimental_only":True,"cost_bps_round_trip":list(GRID),
        "bootstrap_replicates":n_bootstrap,"bootstrap_block_observations":24,
        "seed":seed,"source_custody_verified":anchor is not None,
        "custody_attestation_sha256":anchor.rows_sha256 if anchor else None}
