from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.northflank.com/v1"
PROJECT = "seneciobot"
SERVICE = "senecio-h011"
BRANCH = "feat/order-070-runtime-truth-hardening"
HEAD = "4b107bfb427cb85ea84850ffd9ddd5d7a4231d94"
TREE = "5d1d9ec806b7d0e02031726565f08ef75d5a9340"
BUILD_ID = "bumpy-brass-9194"
BUILD_DIGEST = "sha256:8f4511e0ac2499e3b7408843a82e7f3a5bc4cc466c296003eb363842ad2023ac"
IMAGE_DIGEST = "sha256:431702a5e4bb08d139151b5d484428423fa3cc15927d155b768ed2142aee1084"
ORIGIN = "https://h011-web--senecio-h011--wbjggn89fnf8.code.run"
RAM_LIMIT_MB = 512.0
STABILITY_SECONDS = 1800
SAMPLE_SECONDS = 15
ROOT = Path(os.environ.get("CANDIDATE_DIR", "candidate")).resolve()
OUT = Path("order070-r7-final-evidence").resolve()
TOKEN = os.environ["NORTHFLANK_API_TOKEN"]
CI_RUNS = {
    "ORDER070": 32585446334,
    "SCORE001": 32585446345,
    "SCORE002": 32585446326,
    "SMOKE": 32585446328,
}

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None = None) -> str:
    return (t or now()).isoformat().replace("+00:00", "Z")


def h256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def write(name: str, obj) -> str:
    p = OUT / name
    p.write_text(json.dumps(obj, sort_keys=True, indent=2, default=str) + "\n")
    return h256(p.read_bytes())


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()


def request_json(method: str, url: str, headers=None, payload=None, timeout=60):
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(
        url,
        headers=headers or {"Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "senex-order070-r7/1"},
        data=body,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            raw = r.read()
            rh = {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        status = e.code
        raw = e.read()
        rh = {k.lower(): v for k, v in e.headers.items()}
    except Exception as e:
        return {"http": 0, "body": {"error_type": type(e).__name__, "error": str(e)[:200]}, "headers": {}, "sha256": None}
    try:
        obj = json.loads(raw.decode())
    except Exception:
        obj = {"_non_json": True, "bytes": len(raw), "sha256": h256(raw), "text": raw.decode(errors="replace")[:300]}
    return {"http": status, "body": obj, "headers": rh, "sha256": h256(raw)}


def pub(base: str, path: str, method="GET", payload=None, timeout=45):
    return request_json(method, base.rstrip("/") + path, None, payload, timeout)


def nf(path: str, query=None, timeout=90):
    # ORDER-070-R7 hard guarantee: this continuation is Northflank GET-only.
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/json",
        "User-Agent": "senex-order070-r7-nf-readonly/1",
    }
    r = request_json("GET", url, headers, None, timeout)
    if not 200 <= r["http"] < 300:
        raise RuntimeError(f"NF_GET_{path}_HTTP_{r['http']}:{str(r['body'])[:220]}")
    x = r["body"]
    return (x.get("data", x) if isinstance(x, dict) else x), r


def containers():
    x, _ = nf(f"/projects/{PROJECT}/services/{SERVICE}/containers", {"per_page": 100})
    return (x.get("containers") if isinstance(x, dict) else x) or []


def running_names(rows):
    return sorted(str(x.get("name")) for x in rows if isinstance(x, dict) and x.get("status") == "TASK_RUNNING")


def cf_env():
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("CLOUDFLARE_") or k in {"CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_API_KEY", "CF_EMAIL"}:
            env.pop(k, None)
    return env


def deploy_temp_worker():
    attempts = []
    for attempt in range(1, 4):
        cp = subprocess.run(
            ["npx", "--yes", "wrangler@4.102.0", "deploy", "--temporary", "--config", "wrangler.jsonc"],
            cwd=ROOT / "edge/order070",
            env=cf_env(),
            text=True,
            capture_output=True,
            timeout=180,
        )
        raw = (cp.stdout or "") + "\n" + (cp.stderr or "")
        digest = h256(raw.encode())
        urls = re.findall(r"https://[A-Za-z0-9._-]+\.workers\.dev", raw)
        attempts.append({"attempt": attempt, "exit": cp.returncode, "output_sha256": digest, "url_found": bool(urls)})
        if cp.returncode == 0 and urls:
            return urls[-1].rstrip("/"), digest, attempts
        time.sleep(2)
    raise RuntimeError(f"CLOUDFLARE_TEMP_DEPLOY_FAILED:{attempts}")


def curl_probe(base: str, method: str, path: str):
    cp = subprocess.run(
        ["curl", "-sS", "--max-time", "40", "-D", "/tmp/r7-headers", "-o", "/tmp/r7-body", "-w", "%{http_code}", "-X", method, base.rstrip("/") + path],
        text=True,
        capture_output=True,
        timeout=45,
        env=cf_env(),
    )
    code = int(cp.stdout.strip()) if cp.returncode == 0 and cp.stdout.strip().isdigit() else 0
    decision = None
    hp = Path("/tmp/r7-headers")
    if hp.exists():
        for line in hp.read_text(errors="replace").splitlines():
            if line.lower().startswith("x-senex-edge-decision:"):
                decision = line.split(":", 1)[1].strip()
    bp = Path("/tmp/r7-body")
    return {"http": code, "decision": decision, "curl_exit": cp.returncode, "body_sha256": h256(bp.read_bytes()) if bp.exists() else None}


def identity(kind: str, body: dict):
    if kind == "snapshot":
        return body.get("snapshot_id"), body.get("generation"), body.get("canonical_sha256")
    if kind == "ready":
        return body.get("authority_snapshot_id"), body.get("generation"), body.get("canonical_sha256")
    return body.get("authority_snapshot_id"), body.get("authority_generation"), body.get("authority_canonical_sha256")


def assert_safety(health, ready, prov, state=None):
    if health["http"] != 200 or ready["http"] != 200 or prov["http"] != 200:
        raise RuntimeError(f"LIVE_HTTP:{health['http']}:{ready['http']}:{prov['http']}")
    hb, rb, pb = health["body"], ready["body"], prov["body"]
    if hb.get("trade_mode") != "PAPER" or hb.get("orders_enabled") is not False or hb.get("live_capital_locked") is not True:
        raise RuntimeError("PAPER_LOCK_FAILED")
    if rb.get("status") != "ready" or not all((rb.get("checks") or {}).values()):
        raise RuntimeError(f"READY_FAILED:{rb}")
    if pb.get("exact") is not True or pb.get("source_commit") != HEAD or pb.get("source_tree") != TREE or pb.get("build_digest") != BUILD_DIGEST or pb.get("image_digest") != IMAGE_DIGEST:
        raise RuntimeError(f"PROVENANCE_FAILED:{pb}")
    if state is not None:
        sb = state["body"]
        if sb.get("trade_mode") != "PAPER" or sb.get("live_capital_locked") is not True:
            raise RuntimeError("STATE_SAFETY_FAILED")


def metric_points(metrics, metric_name: str):
    obj = (metrics or {}).get(metric_name, {}) if isinstance(metrics, dict) else {}
    pts = []
    for series in obj.get("values", []) if isinstance(obj, dict) else []:
        meta = series.get("metadata") or {}
        for p in series.get("data") or []:
            try:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    ts, val = p[0], float(p[1])
                else:
                    ts, val = p.get("timestamp") or p.get("time") or p.get("ts"), float(p.get("value"))
                pts.append({"container": meta.get("containerId"), "ts": ts, "value": val})
            except Exception:
                pass
    return pts, ((obj.get("metricInfo") or {}).get("metricUnit") if isinstance(obj, dict) else None)


# Immutable candidate identity.
if git("rev-parse", "HEAD") != HEAD or git("rev-parse", "HEAD^{tree}") != TREE or git("status", "--porcelain"):
    raise RuntimeError("CANDIDATE_DRIFT")
remote = git("ls-remote", "origin", f"refs/heads/{BRANCH}").split()[0]
if remote != HEAD:
    raise RuntimeError(f"REMOTE_HEAD_DRIFT:{remote}")
write("REMOTE_TRUTH.json", {"observed_at": iso(), "order": "ORDER-070-R7", "pr": 67, "head": HEAD, "tree": TREE, "candidate_change": False, "ops_harness_only": True, "merge": False, "ci_runs": CI_RUNS})

# Verify repository-scoped Northflank auth and already-deployed exact origin. GET-only.
_, project_get = nf(f"/projects/{PROJECT}")
dep, dep_get = nf(f"/projects/{PROJECT}/services/{SERVICE}/deployment")
build, build_get = nf(f"/projects/{PROJECT}/services/{SERVICE}/build/{BUILD_ID}")
svc, svc_get = nf(f"/projects/{PROJECT}/services/{SERVICE}")
services, _ = nf(f"/projects/{PROJECT}/services", {"per_page": 100})
arr = services.get("services") if isinstance(services, dict) else services
entry = next(x for x in arr if x.get("id") == SERVICE)
deployed_sha = (dep.get("internal") or {}).get("deployedSHA")
deployment_status = ((entry.get("status") or {}).get("deployment") or {}).get("status")
docker = ((svc.get("buildSettings") or {}).get("dockerfile") or {})
if not build.get("concluded") or not build.get("success") or build.get("sha") != HEAD:
    raise RuntimeError(f"EXACT_BUILD_READBACK_FAILED:{build.get('sha')}:{build.get('success')}")
if deployed_sha != HEAD or deployment_status != "COMPLETED":
    raise RuntimeError(f"EXACT_DEPLOY_READBACK_FAILED:{deployed_sha}:{deployment_status}")
if (svc.get("vcsData") or {}).get("projectBranch") != "main" or entry.get("disabledCI") is not True:
    raise RuntimeError("NORTHFLANK_SOURCE_STATE_DRIFT")
if docker.get("dockerFilePath") != "/Dockerfile" or docker.get("dockerWorkDir") != "/":
    raise RuntimeError(f"CANONICAL_DOCKER_DRIFT:{docker}")
write("NORTHFLANK_EXACT_READBACK.json", {"observed_at": iso(), "auth_project_http": project_get["http"], "auth_deployment_http": dep_get["http"], "build_http": build_get["http"], "service_http": svc_get["http"], "secret_value_observed": False, "northflank_mutations": 0, "build_id": BUILD_ID, "build_sha": build.get("sha"), "build_success": True, "deployed_sha": deployed_sha, "deployment_status": deployment_status, "source_branch": "main", "docker_file_path": docker.get("dockerFilePath"), "docker_work_dir": docker.get("dockerWorkDir"), "build_digest": BUILD_DIGEST, "image_digest": IMAGE_DIGEST})

# Bounded exact-origin observational gate; no manual restart/redeploy.
origin_attempts = []
for attempt in range(1, 31):
    snapshot = pub(ORIGIN, "/api/authority/snapshot?symbol=BTCUSDT")
    health = pub(ORIGIN, "/healthz")
    ready = pub(ORIGIN, "/readyz?symbol=BTCUSDT")
    prov = pub(ORIGIN, "/api/runtime/provenance")
    state = pub(ORIGIN, "/api/oracle/state?symbol=BTCUSDT")
    origin_attempts.append({"attempt": attempt, "snapshot": snapshot["http"], "health": health["http"], "ready": ready["http"], "provenance": prov["http"], "state": state["http"]})
    if all(x["http"] == 200 for x in [snapshot, health, ready, prov, state]):
        assert_safety(health, ready, prov, state)
        break
    time.sleep(2)
else:
    write("ORIGIN_GATE_FAILED.json", {"observed_at": iso(), "attempts": origin_attempts})
    raise RuntimeError("ORIGIN_EXACT_GATE_TIMEOUT")
write("ORIGIN_LIVE.json", {"observed_at": iso(), "attempts": origin_attempts, "healthz": 200, "readyz": 200, "provenance_exact": True, "evidence_status": "EXACT_HEAD_BOUND", "snapshot_id": snapshot["body"].get("snapshot_id"), "generation": snapshot["body"].get("generation"), "canonical_sha256": snapshot["body"].get("canonical_sha256"), "exact_total_predictions": snapshot["body"].get("exact_total_predictions")})

# Fresh exact-head temporary edge. Direct public endpoint probes only; no wrangler dev --remote.
edge, deploy_output_sha, deploy_attempts = deploy_temp_worker()
boot_attempts = []
for attempt in range(1, 41):
    boot = pub(edge, "/healthz")
    boot_attempts.append({"attempt": attempt, "http": boot["http"], "decision": boot["headers"].get("x-senex-edge-decision")})
    if boot["http"] == 200 and boot["headers"].get("x-senex-edge-decision") == "ALLOW_GET_PROXY":
        break
    time.sleep(2)
else:
    raise RuntimeError(f"EDGE_BOOT_FAILED:{boot_attempts}")
post = curl_probe(edge, "POST", "/api/oracle/score")
unknown = curl_probe(edge, "GET", "/__order070_unknown__")
if post["http"] != 405 or post["decision"] != "DENY_METHOD":
    raise RuntimeError(f"EDGE_POST_DENIAL_FAILED:{post}")
if unknown["http"] != 404 or unknown["decision"] != "DENY_PATH":
    raise RuntimeError(f"EDGE_UNKNOWN_DENIAL_FAILED:{unknown}")
write("CLOUDFLARE_FINAL.json", {"observed_at": iso(), "head": HEAD, "tree": TREE, "temporary_worker_url": edge, "deploy_output_sha256": deploy_output_sha, "deploy_attempts": deploy_attempts, "boot_attempts": boot_attempts, "positive_allowlist": {"http": 200, "decision": "ALLOW_GET_PROXY"}, "post_denial": post, "unknown_denial": unknown, "wrangler_remote_dev_used": False, "cloudflare_credentials_used_for_probes": False, "incremental_spend_usd": 0, "result": "PASS"})

# >=8 concurrent origin <-> edge reconciliation rounds.
paths = {
    "snapshot": "/api/authority/snapshot?symbol=BTCUSDT",
    "score": "/api/oracle/score?symbol=BTCUSDT",
    "state": "/api/oracle/state?symbol=BTCUSDT",
    "gate": "/api/portfolio/live_gate?symbol=BTCUSDT",
    "ready": "/readyz?symbol=BTCUSDT",
}

def fetch_job(side, base, kind, path):
    return side, kind, pub(base, path)

rounds = []
for n in range(1, 9):
    prime = pub(ORIGIN, paths["snapshot"])
    if prime["http"] != 200:
        raise RuntimeError(f"ROUND_PRIME_FAILED:{n}:{prime['http']}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        rows = [f.result() for f in [ex.submit(fetch_job, side, base, kind, path) for side, base in [("origin", ORIGIN), ("edge", edge)] for kind, path in paths.items()]]
    got = {(side, kind): r for side, kind, r in rows}
    statuses = {f"{side}:{kind}": got[(side, kind)]["http"] for side in ("origin", "edge") for kind in paths}
    if any(v != 200 for v in statuses.values()):
        raise RuntimeError(f"ROUND_HTTP_FAILED:{n}:{statuses}")
    ids = [identity(kind, got[(side, kind)]["body"]) for side in ("origin", "edge") for kind in paths]
    if any(None in ident for ident in ids) or len(set(ids)) != 1:
        raise RuntimeError(f"ROUND_IDENTITY_MISMATCH:{n}:{ids}")
    osnap = got[("origin", "snapshot")]["body"]
    esnap = got[("edge", "snapshot")]["body"]
    core = ["snapshot_id", "generation", "canonical_sha256", "symbol", "authority_history_complete", "authority_history_rows", "exact_total_predictions", "exact_count_complete", "last_cursor_or_equivalent", "score", "live_gate", "provenance"]
    if not all(osnap.get(k) == esnap.get(k) for k in core):
        raise RuntimeError(f"ROUND_SNAPSHOT_CORE_MISMATCH:{n}")
    if got[("origin", "score")]["body"] != got[("edge", "score")]["body"] or got[("origin", "gate")]["body"] != got[("edge", "gate")]["body"]:
        raise RuntimeError(f"ROUND_PAYLOAD_MISMATCH:{n}")
    rounds.append({"round": n, "statuses": statuses, "snapshot_id": ids[0][0], "generation": ids[0][1], "canonical_sha256": ids[0][2], "all_10_identities_equal": True, "snapshot_core_equal": True, "score_equal": True, "live_gate_equal": True, "exact_total_predictions": osnap.get("exact_total_predictions"), "authority_history_rows": osnap.get("authority_history_rows")})
write("CONCURRENT_RECONCILIATION.json", {"observed_at": iso(), "round_count": len(rounds), "rounds": rounds, "result": "PASS"})

# Live E2E, including dashboard prediction evidence parity.
e2e_paths = {
    "snapshot": paths["snapshot"],
    "context": "/api/market-context?symbol=BTCUSDT",
    "predictions": "/api/oracle/predictions/db?limit=50&symbol=BTCUSDT",
    "provenance": "/api/runtime/provenance",
    "health": "/healthz",
    "ready": "/readyz?symbol=BTCUSDT",
    "openapi": "/openapi.json",
}
final = {k: {"origin": pub(ORIGIN, p), "edge": pub(edge, p)} for k, p in e2e_paths.items()}
for k in e2e_paths:
    if final[k]["origin"]["http"] != 200 or final[k]["edge"]["http"] != 200:
        raise RuntimeError(f"E2E_HTTP:{k}:{final[k]['origin']['http']}:{final[k]['edge']['http']}")
if final["predictions"]["origin"]["body"] != final["predictions"]["edge"]["body"]:
    raise RuntimeError("E2E_PREDICTION_PARITY_FAILED")
for side in ("origin", "edge"):
    p = final["provenance"][side]["body"]
    h = final["health"][side]["body"]
    r = final["ready"][side]["body"]
    saf = final["context"][side]["body"].get("safety") or {}
    schema = final["openapi"][side]["body"]
    unsafe = sum(1 for _, item in (schema.get("paths") or {}).items() if isinstance(item, dict) for m in item if str(m).lower() in {"post", "put", "patch", "delete"})
    if p.get("exact") is not True or p.get("source_commit") != HEAD or p.get("source_tree") != TREE or p.get("build_digest") != BUILD_DIGEST or p.get("image_digest") != IMAGE_DIGEST:
        raise RuntimeError(f"E2E_PROVENANCE:{side}")
    if r.get("status") != "ready" or not all((r.get("checks") or {}).values()):
        raise RuntimeError(f"E2E_READY:{side}")
    if h.get("trade_mode") != "PAPER" or h.get("orders_enabled") is not False or h.get("live_capital_locked") is not True:
        raise RuntimeError(f"E2E_HEALTH:{side}")
    if saf.get("trade_mode") != "PAPER" or saf.get("orders_enabled") is not False or saf.get("live_capital_locked") is not True or saf.get("allow_live") is not False:
        raise RuntimeError(f"E2E_SAFETY:{side}")
    if unsafe != 0:
        raise RuntimeError(f"E2E_UNSAFE_SURFACE:{side}:{unsafe}")
write("LIVE_E2E.json", {"observed_at": iso(), "head": HEAD, "tree": TREE, "build_id": BUILD_ID, "build_digest": BUILD_DIGEST, "image_digest": IMAGE_DIGEST, "origin": ORIGIN, "edge": edge, "healthz_origin": 200, "healthz_edge": 200, "readyz_origin": 200, "readyz_edge": 200, "provenance_exact_origin": True, "provenance_exact_edge": True, "evidence_status": "EXACT_HEAD_BOUND", "authority_snapshot_id": rounds[-1]["snapshot_id"], "concurrent_rounds": 8, "predictions_body_parity": True, "public_unsafe_count": 0, "trade_mode": "PAPER", "orders_enabled": False, "live_capital_locked": True})

# Mandatory >=30m continuous production stability. Rollout occurred before this R7 run and is excluded.
stability_start = now()
start_iso = iso(stability_start)
initial_running = running_names(containers())
if not initial_running:
    raise RuntimeError("NO_RUNNING_CONTAINER_AT_STABILITY_START")
base_snap = pub(ORIGIN, paths["snapshot"])
base_state = pub(ORIGIN, paths["state"])
base_ready = pub(ORIGIN, paths["ready"])
base_health = pub(ORIGIN, "/healthz")
base_prov = pub(ORIGIN, "/api/runtime/provenance")
assert_safety(base_health, base_ready, base_prov, base_state)
base_cycles = int(base_state["body"].get("cycles_run") or 0)
base_db = int(base_snap["body"].get("exact_total_predictions") or 0)
base_last_prediction_ts = base_state["body"].get("last_prediction_ts")
base_btc_rows = int(base_snap["body"].get("authority_history_rows") or 0)
samples = []
generation_last = int(base_snap["body"].get("generation") or 0)
next_sample = time.monotonic()
while (now() - stability_start).total_seconds() < STABILITY_SECONDS:
    delay = next_sample - time.monotonic()
    if delay > 0:
        time.sleep(delay)
    at = now()
    snap = pub(ORIGIN, paths["snapshot"])
    health = pub(ORIGIN, "/healthz")
    ready = pub(ORIGIN, paths["ready"])
    prov = pub(ORIGIN, "/api/runtime/provenance")
    state = pub(ORIGIN, paths["state"])
    edge_health = pub(edge, "/healthz")
    row = {"at": iso(at), "snapshot_http": snap["http"], "health_http": health["http"], "ready_http": ready["http"], "provenance_http": prov["http"], "state_http": state["http"], "edge_health_http": edge_health["http"]}
    if not all(x["http"] == 200 for x in [snap, health, ready, prov, state, edge_health]):
        samples.append(row)
        write("STABILITY_SAMPLES_PARTIAL.json", samples)
        raise RuntimeError(f"STABILITY_HTTP_FAILURE:{row}")
    if edge_health["headers"].get("x-senex-edge-decision") != "ALLOW_GET_PROXY":
        raise RuntimeError(f"STABILITY_EDGE_DECISION_DRIFT:{edge_health['headers']}")
    assert_safety(health, ready, prov, state)
    sid = snap["body"].get("snapshot_id")
    gen = int(snap["body"].get("generation") or 0)
    rid = ready["body"].get("authority_snapshot_id")
    stateid = state["body"].get("authority_snapshot_id")
    if not sid or rid != sid or stateid != sid or gen < generation_last:
        raise RuntimeError(f"STABILITY_SNAPSHOT_INCONSISTENT:{sid}:{rid}:{stateid}:{gen}:{generation_last}")
    generation_last = gen
    row.update({"snapshot_id": sid, "generation": gen, "canonical_sha256": snap["body"].get("canonical_sha256"), "cycles_run": state["body"].get("cycles_run"), "db_predictions": snap["body"].get("exact_total_predictions"), "authority_history_rows": snap["body"].get("authority_history_rows"), "last_prediction_ts": state["body"].get("last_prediction_ts"), "snapshot_stale": ready["body"].get("snapshot_stale"), "last_refresh_error": ready["body"].get("last_refresh_error")})
    if row["snapshot_stale"] is not False or row["last_refresh_error"] is not None:
        raise RuntimeError(f"STABILITY_AUTHORITY_REFRESH_FAILED:{row}")
    if len(samples) % 2 == 0:
        current_running = running_names(containers())
        row["running_containers"] = current_running
        if current_running != initial_running:
            samples.append(row)
            write("STABILITY_SAMPLES_PARTIAL.json", samples)
            raise RuntimeError(f"UNEXPECTED_CONTAINER_REPLACEMENT:{initial_running}:{current_running}")
    samples.append(row)
    next_sample += SAMPLE_SECONDS

stability_end = now()
end_iso = iso(stability_end)
final_snap = pub(ORIGIN, paths["snapshot"])
final_state = pub(ORIGIN, paths["state"])
final_ready = pub(ORIGIN, paths["ready"])
final_health = pub(ORIGIN, "/healthz")
final_prov = pub(ORIGIN, "/api/runtime/provenance")
assert_safety(final_health, final_ready, final_prov, final_state)
final_cycles = int(final_state["body"].get("cycles_run") or 0)
final_db = int(final_snap["body"].get("exact_total_predictions") or 0)
final_last_prediction_ts = final_state["body"].get("last_prediction_ts")
final_btc_rows = int(final_snap["body"].get("authority_history_rows") or 0)
if final_cycles <= base_cycles:
    raise RuntimeError(f"ORACLE_CYCLES_DID_NOT_ADVANCE:{base_cycles}:{final_cycles}")
if final_db <= base_db:
    raise RuntimeError(f"DB_PREDICTIONS_DID_NOT_INCREASE:{base_db}:{final_db}")
if not final_last_prediction_ts or final_last_prediction_ts == base_last_prediction_ts:
    raise RuntimeError(f"LATEST_PREDICTION_TIMESTAMP_DID_NOT_ADVANCE:{base_last_prediction_ts}:{final_last_prediction_ts}")
if final_btc_rows < base_btc_rows:
    raise RuntimeError(f"BTC_AUTHORITY_ROWS_DECREASED:{base_btc_rows}:{final_btc_rows}")
final_running = running_names(containers())
if final_running != initial_running:
    raise RuntimeError(f"FINAL_CONTAINER_IDENTITY_DRIFT:{initial_running}:{final_running}")

# Northflank metrics/logs are GET-only and scoped strictly to the R7 stability window.
metrics, _ = nf(
    f"/projects/{PROJECT}/services/{SERVICE}/metrics",
    [("queryType", "range"), ("startTime", start_iso), ("endTime", end_iso), ("metricTypes", "memory"), ("metricTypes", "requests"), ("metricTypes", "http5xxResponses"), ("metricTypes", "tcpConnectionsOpen")],
)
mempts, memunit = metric_points(metrics, "memory")
relevant_mem = [p for p in mempts if not p.get("container") or p.get("container") in initial_running]
if not relevant_mem:
    raise RuntimeError(f"NO_MEMORY_METRICS:{memunit}:{initial_running}")
if (memunit or "").lower() == "mb":
    for p in relevant_mem:
        p["pct"] = p["value"] / RAM_LIMIT_MB * 100.0
else:
    for p in relevant_mem:
        p["pct"] = p["value"]
ram_max = max(p["pct"] for p in relevant_mem)
if ram_max >= 90.0:
    raise RuntimeError(f"RAM_MAX_NOT_BELOW_90:{ram_max}")
five_xx, _ = metric_points(metrics, "http5xxResponses")
relevant_5xx = [p for p in five_xx if not p.get("container") or p.get("container") in initial_running]
max_5xx = max([p["value"] for p in relevant_5xx], default=0.0)
if max_5xx > 0:
    raise RuntimeError(f"HTTP5XX_NONZERO:{max_5xx}")
logs, _ = nf(f"/projects/{PROJECT}/services/{SERVICE}/logs", {"queryType": "range", "startTime": start_iso, "endTime": end_iso, "type": "runtime", "lineLimit": 1000, "direction": "forward"})
rows = logs if isinstance(logs, list) else []
patterns = {
    "connection_refused": re.compile(r"connection refused|connect error|upstream connect error|disconnect/reset before headers|remote connection failure", re.I),
    "oom": re.compile(r"oom|out of memory|oomkilled|killed process|memory cgroup|MemoryError", re.I),
    "process_exit": re.compile(r"uvicorn exited|Process terminated|exit code|process exited|container exited|TASK_KILLED", re.I),
}
matches = {k: [] for k in patterns}
for row in rows:
    text = str(row.get("log") or "") if isinstance(row, dict) else str(row)
    for k, pat in patterns.items():
        if pat.search(text):
            matches[k].append({"ts": row.get("ts") if isinstance(row, dict) else None, "containerId": row.get("containerId") if isinstance(row, dict) else None, "log": text[:500]})
if any(matches.values()):
    raise RuntimeError(f"STABILITY_RUNTIME_EVENT:{ {k: len(v) for k, v in matches.items()} }")
write("STABILITY_30M.json", {"observed_at": iso(), "start": start_iso, "end": end_iso, "duration_seconds": (stability_end - stability_start).total_seconds(), "sample_interval_seconds": SAMPLE_SECONDS, "sample_count": len(samples), "initial_running_containers": initial_running, "final_running_containers": final_running, "unexpected_restarts": 0, "unexpected_process_exits": 0, "oom_kills": 0, "connection_refused": 0, "http5xx_max_metric": max_5xx, "healthz_continuous": "PASS", "readyz_continuous": "PASS", "edge_health_continuous": "PASS", "authority_refresh_continuous": "PASS", "snapshot_generation_consistent": "PASS", "ram_metric_unit": memunit, "ram_max_pct": ram_max, "ram_points": len(relevant_mem), "oracle_cycles_initial": base_cycles, "oracle_cycles_final": final_cycles, "oracle_cycles_advance": final_cycles - base_cycles, "db_predictions_initial": base_db, "db_predictions_final": final_db, "db_predictions_increase": final_db - base_db, "latest_prediction_ts_initial": base_last_prediction_ts, "latest_prediction_ts_final": final_last_prediction_ts, "latest_prediction_ts_advanced": True, "btc_authority_rows_initial": base_btc_rows, "btc_authority_rows_final": final_btc_rows, "btc_authority_rows_nondecreasing": True, "runtime_log_rows": len(rows), "runtime_matches": {k: len(v) for k, v in matches.items()}, "orders_enabled": False, "real_order_count": 0, "real_capital_movement": 0, "samples": samples})

summary = {
    "observed_at": iso(),
    "order": "ORDER-070-R7",
    "status": "READY_FOR_AUD",
    "pr": 67,
    "head": HEAD,
    "tree": TREE,
    "candidate_change": False,
    "ops_harness_only": True,
    "northflank_redeploy": False,
    "build_id": BUILD_ID,
    "build_digest": BUILD_DIGEST,
    "image_digest": IMAGE_DIGEST,
    "origin_deploy": "REUSED_EXACT_PASS",
    "healthz": 200,
    "readyz": 200,
    "provenance": "EXACT_HEAD_BOUND",
    "cloudflare_final_exact_head": "PASS",
    "snapshot_reconciliation": "PASS_8_ROUNDS",
    "live_e2e": "PASS",
    "ram_max_pct_30m": ram_max,
    "unexpected_restarts_30m": 0,
    "unexpected_process_exits_30m": 0,
    "oom_kills_30m": 0,
    "connection_refused_30m": 0,
    "health_continuity_30m": "PASS",
    "ready_continuity_30m": "PASS",
    "authority_refresh_continuous_30m": "PASS",
    "oracle_cycles_advance": final_cycles - base_cycles,
    "db_predictions_increase": final_db - base_db,
    "latest_prediction_ts_advanced": True,
    "btc_authority_rows_nondecreasing": True,
    "real_order_count": 0,
    "real_capital_movement": 0,
    "supabase_data_mutation": 0,
    "runtime017_mutation": 0,
    "tuning": 0,
    "merge": False,
}
write("FINAL_GATE_SUMMARY.json", summary)
required = [
    "REMOTE_TRUTH.json",
    "NORTHFLANK_EXACT_READBACK.json",
    "ORIGIN_LIVE.json",
    "CLOUDFLARE_FINAL.json",
    "CONCURRENT_RECONCILIATION.json",
    "LIVE_E2E.json",
    "STABILITY_30M.json",
    "FINAL_GATE_SUMMARY.json",
]
manifest = OUT / "MANIFEST.sha256"
manifest.write_text("\n".join(f"{h256((OUT / n).read_bytes())}  {n}" for n in sorted(required)) + "\n")
ck = subprocess.run(["sha256sum", "-c", "MANIFEST.sha256"], cwd=OUT, text=True, capture_output=True)
if ck.returncode != 0:
    raise RuntimeError(f"MANIFEST_VERIFY_FAILED:{ck.stdout}:{ck.stderr}")
manifest_sha = h256(manifest.read_bytes())
print("ORDER_070_STATUS=READY_FOR_AUD")
print(f"PR=67")
print(f"HEAD={HEAD}")
print(f"TREE={TREE}")
print(f"BUILD_ID={BUILD_ID}")
print(f"OCI_DIGEST={IMAGE_DIGEST}")
print("HEALTHZ=200")
print("READYZ=200")
print("CLOUDFLARE_FINAL_EXACT_HEAD=PASS")
print("SNAPSHOT_RECONCILIATION=PASS_8_ROUNDS")
print("PROVENANCE=EXACT_HEAD_BOUND")
print("LIVE_E2E=PASS")
print(f"RAM_MAX_PCT_30M={ram_max:.6f}")
print("UNEXPECTED_RESTARTS_30M=0")
print("OOM_KILLS_30M=0")
print("CONNECTION_REFUSED_30M=0")
print("HEALTH_CONTINUITY_30M=PASS")
print("READY_CONTINUITY_30M=PASS")
print(f"ORACLE_CYCLES_ADVANCE={final_cycles - base_cycles}")
print(f"DB_PREDICTIONS_INCREASE={final_db - base_db}")
print("LATEST_PREDICTION_TIMESTAMP_ADVANCED=YES")
print("BTC_AUTHORITY_ROWS_NONDECREASING=YES")
print(f"MANIFEST_SHA256={manifest_sha}")
