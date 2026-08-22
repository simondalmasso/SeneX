from __future__ import annotations

from pathlib import Path

SOURCE = Path(__file__).with_name("order070_r6_terminal.py")
s = SOURCE.read_text()


def one(old: str, new: str, label: str) -> None:
    global s
    n = s.count(old)
    if n != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {n}")
    s = s.replace(old, new, 1)


# Exact R8 identity. 750d is the empty native-CI trigger commit over bb99;
# both commits share the same candidate tree.
one(
    "import concurrent.futures, datetime as dt, hashlib, json, os, re, shutil, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request",
    "import concurrent.futures, copy, datetime as dt, hashlib, json, os, re, shutil, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request",
    "copy import",
)
one(
    "HEAD='d166495e9a74f528ccce1adeb5ce97a281b175cf'; TREE='6106f1c2f39b4509d3a237eb807db5d45feb7463'",
    "HEAD='750d6042fd1d983c30ca14c503ea09544fc62850'; TREE='16d8c8943bdb7dca2b76d78328df4d083e6512df'",
    "R8 exact identity",
)
one(
    "BUILD_DIGEST='sha256:1806ad0bc71c45264695c1c8973a497a39f9903f867ece2d56fdbc12f44e4892'",
    "BUILD_DIGEST=os.environ['BUILD_DIGEST']",
    "dynamic canonical build digest",
)
one(
    "OUT=Path('order070-r6-final-evidence').resolve()",
    "OUT=Path('order070-r8-final-evidence').resolve()",
    "R8 evidence directory",
)

# R8 scope and exact native-CI evidence. No decision bridge, tuning, RUNTIME017,
# capital, wallet or order files are permitted in the R8 delta.
old_scope = """changed=git('diff','--name-only','483b389a83610992800181c0a21b5a337009f7b4..HEAD').splitlines()
if changed!=['senecio_polymarket/backend/main.py']: raise RuntimeError(f'R6_SCOPE_DRIFT:{changed}')
write('REMOTE_TRUTH.json',{'observed_at':iso(),'pr':67,'head':HEAD,'tree':TREE,'parent':'483b389a83610992800181c0a21b5a337009f7b4','changed_since_r5':changed,'candidate_scope':'OPTIONAL_ANALYTICS_LAZY_INIT_ONLY','merge':False,'tuning':0,'runtime017_mutation':0,'supabase_data_mutation':0})
write('EXACT_GATE.json',{'observed_at':iso(),'workflow_run_id':os.environ.get('GITHUB_RUN_ID'),'head':HEAD,'tree':TREE,'gate':'PASS','native_pr_runs_action_required_without_jobs':True,'equivalent_original_workflow_commands_executed':True,'import_rss_kb':int(os.environ.get('R6_IMPORT_RSS_KB','0') or 0),'import_rss_limit_kb':81920})
"""
new_scope = """changed=sorted(git('diff','--name-only','4b107bfb427cb85ea84850ffd9ddd5d7a4231d94..HEAD').splitlines())
expected=sorted([
    'senecio_polymarket/backend/authority_snapshot.py',
    'senecio_polymarket/backend/main_real.py',
    'senecio_polymarket/backend/supabase_client.py',
    'senecio_polymarket/frontend/app.js',
    'senecio_polymarket/tests/test_order_070_r8.py',
])
if changed!=expected: raise RuntimeError(f'R8_SCOPE_DRIFT:{changed}')
write('REMOTE_TRUTH.json',{'observed_at':iso(),'order':'ORDER-070-R8','pr':67,'head':HEAD,'tree':TREE,'parent_candidate':'4b107bfb427cb85ea84850ffd9ddd5d7a4231d94','changed_from_old_candidate':changed,'candidate_scope':'R8_MEMORY_PROJECTION_AND_READINESS_TRUTH','merge':False,'tuning':0,'runtime017_mutation':0,'supabase_data_mutation':0,'real_order_count':0,'real_capital_movement':0})
write('EXACT_GATE.json',{'observed_at':iso(),'workflow_run_id':os.environ.get('GITHUB_RUN_ID'),'head':HEAD,'tree':TREE,'gate':'PASS','native_exact_head_ci':'PASS_ALL_4','ci_runs':{'ORDER070':32601225258,'SCORE001':32601225277,'SCORE002':32601225290,'SMOKE':32601225208},'canonical_build_digest':BUILD_DIGEST})
"""
one(old_scope, new_scope, "R8 exact scope")

# Preserve Northflank's non-target build settings while forcing the one canonical
# root Dockerfile for this exact candidate build. Source is restored to main.
old_service = "e0=services_entry(); s0=service(); d0=deployment(); vcs=s0.get('vcsData') or {}; original=vcs.get('projectBranch')\nif s0.get('serviceType')!='combined' or original!='main' or e0.get('disabledCI') is not True: raise RuntimeError('NORTHFLANK_PREFLIGHT')\nvpatch={k:vcs[k] for k in ('accountLogin','vcsLinkId','selfHostedVcsId') if vcs.get(k)}; vpatch.update({'projectUrl':vcs['projectUrl'],'projectType':vcs['projectType'],'projectBranch':BRANCH})\n"
new_service = "e0=services_entry(); s0=service(); d0=deployment(); vcs=s0.get('vcsData') or {}; original=vcs.get('projectBranch')\nif s0.get('serviceType')!='combined' or original!='main' or e0.get('disabledCI') is not True: raise RuntimeError('NORTHFLANK_PREFLIGHT')\noriginal_build_settings=copy.deepcopy(s0.get('buildSettings') or {})\ntarget_build_settings=copy.deepcopy(original_build_settings)\ndocker_settings=copy.deepcopy(target_build_settings.get('dockerfile') or {})\ndocker_settings.update({'dockerFilePath':'/Dockerfile','dockerWorkDir':'/'})\ntarget_build_settings['dockerfile']=docker_settings\nvpatch={k:vcs[k] for k in ('accountLogin','vcsLinkId','selfHostedVcsId') if vcs.get(k)}; vpatch.update({'projectUrl':vcs['projectUrl'],'projectType':vcs['projectType'],'projectBranch':BRANCH})\n"
one(old_service, new_service, "canonical root Dockerfile setup")
one(
    "nf('PATCH',f'/projects/{PROJECT}/services/combined/{SERVICE}',{'disabledCI':True,'buildSource':'git','vcsData':vpatch}); switched=True",
    "nf('PATCH',f'/projects/{PROJECT}/services/combined/{SERVICE}',{'disabledCI':True,'buildSource':'git','vcsData':vpatch,'buildSettings':target_build_settings}); switched=True",
    "source switch build settings",
)
one(
    "restore=dict(vpatch); restore['projectBranch']=original; nf('PATCH',f'/projects/{PROJECT}/services/combined/{SERVICE}',{'disabledCI':True,'buildSource':'git','vcsData':restore})",
    "restore=dict(vpatch); restore['projectBranch']=original; nf('PATCH',f'/projects/{PROJECT}/services/combined/{SERVICE}',{'disabledCI':True,'buildSource':'git','vcsData':restore,'buildSettings':target_build_settings})",
    "source restore build settings",
)
one(
    "if (service().get('vcsData') or {}).get('projectBranch')!='main' or services_entry().get('disabledCI') is not True: raise RuntimeError('SOURCE_RESTORE_FAILED')",
    "post_restore=service(); post_docker=((post_restore.get('buildSettings') or {}).get('dockerfile') or {})\nif (post_restore.get('vcsData') or {}).get('projectBranch')!='main' or services_entry().get('disabledCI') is not True: raise RuntimeError('SOURCE_RESTORE_FAILED')\nif post_docker.get('dockerFilePath')!='/Dockerfile' or post_docker.get('dockerWorkDir')!='/': raise RuntimeError(f'ROOT_DOCKERFILE_NOT_CANONICAL:{post_docker}')",
    "verify canonical build restore",
)

# R7 proved wrangler dev --remote is a harness failure mode. R8 validates the exact
# deployed temporary Worker directly, with no remote-dev subprocess.
old_remote = """proc=fh=logpath=None
try:
    proc,fh,logpath,remote_edge,remote_boot=start_remote_dev(); post=curl_probe(remote_edge,'POST','/api/oracle/score'); unknown=curl_probe(remote_edge,'GET','/__order070_unknown__')
    if post['http']!=405 or post['decision']!='DENY_METHOD' or unknown['http']!=404 or unknown['decision']!='DENY_PATH': raise RuntimeError(f'EDGE_METHOD_BOUNDARY:{post}:{unknown}')
    fh.flush(); remote_log_sha=h256(logpath.read_bytes())
finally:
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=10)
        except Exception: proc.kill()
    if fh is not None and not fh.closed: fh.close()
write('CLOUDFLARE_FINAL.json',{'observed_at':iso(),'head':HEAD,'tree':TREE,'temporary_worker_url':edge,'temporary_deploy_output_sha256':edge_deploy_sha,'public_get':{'http':boot['http'],'decision':boot['headers'].get('x-senex-edge-decision')},'remote_method_boundary':{'get':{'http':remote_boot['http'],'decision':remote_boot['headers'].get('x-senex-edge-decision')},'post':post,'unknown':unknown,'remote_log_sha256':remote_log_sha},'credentials_used':False})
"""
new_remote = """post=curl_probe(edge,'POST','/api/oracle/score'); unknown=curl_probe(edge,'GET','/__order070_unknown__')
if post['http']!=405 or post['decision']!='DENY_METHOD' or unknown['http']!=404 or unknown['decision']!='DENY_PATH': raise RuntimeError(f'EDGE_METHOD_BOUNDARY:{post}:{unknown}')
write('CLOUDFLARE_FINAL.json',{'observed_at':iso(),'head':HEAD,'tree':TREE,'temporary_worker_url':edge,'temporary_deploy_output_sha256':edge_deploy_sha,'public_get':{'http':boot['http'],'decision':boot['headers'].get('x-senex-edge-decision')},'direct_method_boundary':{'post':post,'unknown':unknown},'wrangler_remote_dev_removed':True,'credentials_used':False})
"""
one(old_remote, new_remote, "remove wrangler remote dev")

# Dashboard prediction route must reconcile through the exact public edge as well.
one(
    "e2e_paths={'snapshot':paths['snapshot'],'context':'/api/market-context?symbol=BTCUSDT','provenance':'/api/runtime/provenance','health':'/healthz','ready':'/readyz?symbol=BTCUSDT','openapi':'/openapi.json'}",
    "e2e_paths={'snapshot':paths['snapshot'],'context':'/api/market-context?symbol=BTCUSDT','predictions':'/api/oracle/predictions/db?limit=50&symbol=BTCUSDT','provenance':'/api/runtime/provenance','health':'/healthz','ready':'/readyz?symbol=BTCUSDT','openapi':'/openapi.json'}",
    "dashboard prediction parity route",
)
one(
    "for side in ('origin','edge'):\n    p=final['provenance'][side]['body'];",
    "if final['predictions']['origin']['body'] != final['predictions']['edge']['body']: raise RuntimeError('EDGE_DASHBOARD_PREDICTIONS_PARITY_FAILED')\nfor side in ('origin','edge'):\n    p=final['provenance'][side]['body'];",
    "dashboard prediction parity assertion",
)

# Stability must show continuing economic-observation progress, not just HTTP health.
one(
    "base_cycles=int(base_state['body'].get('cycles_run') or 0); base_db=int(base_snap['body'].get('exact_total_predictions') or 0); samples=[]; generation_last=int(base_snap['body'].get('generation') or 0); next_sample=time.monotonic()",
    "base_cycles=int(base_state['body'].get('cycles_run') or 0); base_db=int(base_snap['body'].get('exact_total_predictions') or 0); base_last_prediction_ts=base_state['body'].get('last_prediction_ts'); base_btc_rows=int(base_snap['body'].get('authority_history_rows') or 0); samples=[]; generation_last=int(base_snap['body'].get('generation') or 0); next_sample=time.monotonic()",
    "R8 progress baseline",
)
one(
    "final_cycles=int(final_state['body'].get('cycles_run') or 0); final_db=int(final_snap['body'].get('exact_total_predictions') or 0)\nif final_cycles<=base_cycles: raise RuntimeError(f'ORACLE_CYCLES_DID_NOT_ADVANCE:{base_cycles}:{final_cycles}')\nif final_db<=base_db: raise RuntimeError(f'DB_PREDICTIONS_DID_NOT_INCREASE:{base_db}:{final_db}')",
    "final_cycles=int(final_state['body'].get('cycles_run') or 0); final_db=int(final_snap['body'].get('exact_total_predictions') or 0); final_last_prediction_ts=final_state['body'].get('last_prediction_ts'); final_btc_rows=int(final_snap['body'].get('authority_history_rows') or 0)\nif final_cycles<=base_cycles: raise RuntimeError(f'ORACLE_CYCLES_DID_NOT_ADVANCE:{base_cycles}:{final_cycles}')\nif final_db<=base_db: raise RuntimeError(f'DB_PREDICTIONS_DID_NOT_INCREASE:{base_db}:{final_db}')\nif not final_last_prediction_ts or final_last_prediction_ts==base_last_prediction_ts: raise RuntimeError(f'LATEST_PREDICTION_TIMESTAMP_DID_NOT_ADVANCE:{base_last_prediction_ts}:{final_last_prediction_ts}')\nif final_btc_rows<base_btc_rows: raise RuntimeError(f'BTC_AUTHORITY_ROWS_DECREASED:{base_btc_rows}:{final_btc_rows}')",
    "R8 progress final",
)

# Preserve the strict RAM contract verbatim and expose all required statistics.
one(
    "ram_max=max(p['pct'] for p in relevant)\nif ram_max>=90.0: raise RuntimeError(f'RAM_MAX_NOT_BELOW_90:{ram_max}')",
    "vals=sorted(p['pct'] for p in relevant)\ndef q95(xs):\n    pos=(len(xs)-1)*0.95; lo=int(pos); hi=min(lo+1,len(xs)-1); frac=pos-lo; return xs[lo]*(1-frac)+xs[hi]*frac\nram_max=max(vals); ram_p95=q95(vals); points_ge90=sum(1 for v in vals if v>=90.0)\nif ram_max>=90.0 or ram_p95>=90.0 or points_ge90!=0: raise RuntimeError(f'RAM_GATE_FAILED:max={ram_max}:p95={ram_p95}:ge90={points_ge90}')",
    "strict R8 RAM gate",
)
one(
    "'ram_metric_unit':unit,'ram_max_pct':ram_max,'ram_points':len(relevant),'oracle_cycles_initial':base_cycles,'oracle_cycles_final':final_cycles,'oracle_cycles_advance':final_cycles-base_cycles,'db_predictions_initial':base_db,'db_predictions_final':final_db,'db_predictions_increase':final_db-base_db,'runtime_log_rows'",
    "'ram_metric_unit':unit,'ram_max_pct':ram_max,'ram_p95_pct':ram_p95,'ram_points_ge_90':points_ge90,'ram_points':len(relevant),'oracle_cycles_initial':base_cycles,'oracle_cycles_final':final_cycles,'oracle_cycles_advance':final_cycles-base_cycles,'db_predictions_initial':base_db,'db_predictions_final':final_db,'db_predictions_increase':final_db-base_db,'latest_prediction_ts_initial':base_last_prediction_ts,'latest_prediction_ts_final':final_last_prediction_ts,'latest_prediction_ts_advanced':True,'btc_authority_rows_initial':base_btc_rows,'btc_authority_rows_final':final_btc_rows,'btc_authority_rows_nondecreasing':True,'runtime_log_rows'",
    "R8 stability evidence fields",
)

old_summary = "summary={'observed_at':iso(),'order':'ORDER-070-R6','status':'READY_FOR_AUD','pr':67,'head':HEAD,'tree':TREE,'exact_gate':'PASS','exact_build':'PASS','build_id':build_id,'origin_deploy':'PASS','image_digest':image,'healthz':200,'readyz':200,'provenance':'EXACT_HEAD_BOUND','cloudflare_final_exact_head':'PASS','snapshot_reconciliation':'PASS_8_ROUNDS','live_e2e':'PASS','runtime_memory_fix':'OPTIONAL_ANALYTICS_TRUE_LAZY_INIT','ram_max_pct_30m':ram_max,'unexpected_restarts_30m':0,'unexpected_process_exits_30m':0,'oom_kills_30m':0,'connection_refused_30m':0,'health_continuity_30m':'PASS','ready_continuity_30m':'PASS','authority_refresh_continuous_30m':'PASS','oracle_cycles_advance':final_cycles-base_cycles,'db_predictions_increase':final_db-base_db,'real_order_count':0,'real_capital_movement':0,'supabase_data_mutation':0,'runtime017_mutation':0,'tuning':0,'merge':False}"
new_summary = "summary={'observed_at':iso(),'order':'ORDER-070-R8','status':'READY_FOR_AUD','pr':67,'head':HEAD,'tree':TREE,'exact_gate':'PASS','exact_build':'PASS','build_id':build_id,'origin_deploy':'PASS','image_digest':image,'healthz':200,'readyz':200,'provenance':'EXACT_HEAD_BOUND','cloudflare_final_exact_head':'PASS','snapshot_reconciliation':'PASS_8_ROUNDS','live_e2e':'PASS','root_cause':'COMPLETE_AUTHORITY_HISTORY_RETAINED_RICH_ROWS_PLUS_WHOLE_COHORT_JSON_CLONE','runtime_memory_fix':'COMPLETE_PROJECTED_PROOF_SCORE_HISTORY_PLUS_BOUNDED_RECENT50_NO_JSON_ROUNDTRIP','ram_max_pct_30m':ram_max,'ram_p95_pct_30m':ram_p95,'ram_points_ge_90':points_ge90,'unexpected_restarts_30m':0,'unexpected_process_exits_30m':0,'oom_kills_30m':0,'connection_refused_30m':0,'health_continuity_30m':'PASS','ready_continuity_30m':'PASS','authority_refresh_continuous_30m':'PASS','provenance_continuity_30m':'PASS','snapshot_generation_consistent_30m':'PASS','oracle_cycles_advance':final_cycles-base_cycles,'db_predictions_increase':final_db-base_db,'latest_prediction_ts_advanced':True,'btc_authority_rows_nondecreasing':True,'trade_mode':'PAPER','orders_enabled':False,'live_capital_locked':True,'real_order_count':0,'real_capital_movement':0,'supabase_data_mutation':0,'runtime017_mutation':0,'tuning':0,'merge':False}"
one(old_summary, new_summary, "R8 final summary")

# Terminal stdout must expose the strict RAM evidence needed by AUD.
one(
    "print('HEAD='+HEAD); print('TREE='+TREE); print('BUILD_ID='+str(build_id)); print('OCI_DIGEST='+image); print('RAM_MAX_PCT='+f'{ram_max:.4f}'); print('ORACLE_CYCLES_ADVANCE='+str(final_cycles-base_cycles)); print('DB_PREDICTIONS_INCREASE='+str(final_db-base_db)); print('MANIFEST_SHA256='+h256((OUT/'MANIFEST.sha256').read_bytes()))",
    "print('HEAD='+HEAD); print('TREE='+TREE); print('BUILD_ID='+str(build_id)); print('BUILD_DIGEST='+BUILD_DIGEST); print('OCI_DIGEST='+image); print('RAM_MAX_PCT='+f'{ram_max:.4f}'); print('RAM_P95_PCT='+f'{ram_p95:.4f}'); print('POINTS_GE_90='+str(points_ge90)); print('ORACLE_CYCLES_ADVANCE='+str(final_cycles-base_cycles)); print('DB_PREDICTIONS_INCREASE='+str(final_db-base_db)); print('LATEST_PREDICTION_TS_ADVANCED=YES'); print('BTC_AUTHORITY_ROWS_NONDECREASING=YES'); print('MANIFEST_SHA256='+h256((OUT/'MANIFEST.sha256').read_bytes()))",
    "R8 terminal stdout",
)

code = compile(s, str(SOURCE) + "[ORDER070-R8]", "exec")
exec(code, {"__name__": "__main__", "__file__": str(SOURCE)})
