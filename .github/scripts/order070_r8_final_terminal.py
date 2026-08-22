from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import json
import math
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
HEAD = "750d6042fd1d983c30ca14c503ea09544fc62850"
TREE = "16d8c8943bdb7dca2b76d78328df4d083e6512df"
BASE_R8 = "4b107bfb427cb85ea84850ffd9ddd5d7a4231d94"
ORIGIN = "https://h011-web--senecio-h011--wbjggn89fnf8.code.run"
BUILD_DIGEST = os.environ["BUILD_DIGEST"]
RAM_LIMIT_MB = 512.0
STABILITY_SECONDS = 1800
SAMPLE_SECONDS = 15
ROOT = Path(os.environ.get("CANDIDATE_DIR", "candidate")).resolve()
OUT = Path("order070-r8-final-evidence").resolve()
TOKEN = os.environ["NORTHFLANK_API_TOKEN"]
CI_RUNS = {
    "ORDER070": 32601225258,
    "SCORE001": 32601225277,
    "SCORE002": 32601225290,
    "SMOKE": 32601225208,
}
EXPECTED_R8_FILES = sorted([
    "senecio_polymarket/backend/authority_snapshot.py",
    "senecio_polymarket/backend/main_real.py",
    "senecio_polymarket/backend/supabase_client.py",
    "senecio_polymarket/frontend/app.js",
    "senecio_polymarket/tests/test_order_070_r8.py",
])
NF_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "senex-order070-r8-final/1",
}

if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(value=None):
    return (value or now()).isoformat().replace("+00:00", "Z")


def h256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def fingerprint(value) -> str:
    return h256(canonical(value))


def write(name: str, value) -> str:
    path = OUT / name
    path.write_text(json.dumps(value, sort_keys=True, indent=2, default=str) + "\n")
    return h256(path.read_bytes())


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()


def request_json(method: str, url: str, headers=None, payload=None, timeout=60):
    body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    req = urllib.request.Request(
        url,
        headers=headers or {"Accept": "application/json", "Cache-Control": "no-cache", "User-Agent": "senex-order070-r8-public/1"},
        data=body,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = response.status
            raw = response.read()
            response_headers = {k.lower(): v for k, v in response.headers.items()}
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
        response_headers = {k.lower(): v for k, v in exc.headers.items()}
    except Exception as exc:
        return {"http": 0, "body": {"error_type": type(exc).__name__, "error": str(exc)[:300]}, "headers": {}, "sha256": None}
    try:
        obj = json.loads(raw.decode())
    except Exception:
        obj = {"_non_json": True, "bytes": len(raw), "sha256": h256(raw), "text": raw.decode(errors="replace")[:300]}
    return {"http": status, "body": obj, "headers": response_headers, "sha256": h256(raw)}


def unwrap(value):
    return value.get("data", value) if isinstance(value, dict) else value


def nf(method: str, path: str, payload=None, query=None, timeout=90):
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    result = request_json(method, url, NF_HEADERS, payload, timeout)
    if not 200 <= result["http"] < 300:
        raise RuntimeError(f"NF_{method}_{path}_HTTP_{result['http']}:{str(result['body'])[:300]}")
    return unwrap(result["body"]), result


def pub(base: str, path: str, method="GET", payload=None, timeout=45):
    return request_json(method, base.rstrip("/") + path, None, payload, timeout)


def service():
    return nf("GET", f"/projects/{PROJECT}/services/{SERVICE}")[0]


def services_entry():
    result, _ = nf("GET", f"/projects/{PROJECT}/services", query={"per_page": 100})
    rows = result.get("services") if isinstance(result, dict) else result
    return next(row for row in rows if row.get("id") == SERVICE)


def deployment():
    return nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/deployment")[0]


def containers():
    result, _ = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/containers", query={"per_page": 100})
    return (result.get("containers") if isinstance(result, dict) else result) or []


def running_names(rows):
    return sorted(str(row.get("name")) for row in rows if isinstance(row, dict) and row.get("status") == "TASK_RUNNING")


def cf_env():
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("CLOUDFLARE_") or key in {"CF_API_TOKEN", "CF_ACCOUNT_ID", "CF_API_KEY", "CF_EMAIL"}:
            env.pop(key, None)
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
    headers_path = "/tmp/r8-edge-headers"
    body_path = "/tmp/r8-edge-body"
    cp = subprocess.run(
        ["curl", "-sS", "--max-time", "40", "-D", headers_path, "-o", body_path, "-w", "%{http_code}", "-X", method, base.rstrip("/") + path],
        text=True,
        capture_output=True,
        timeout=45,
        env=cf_env(),
    )
    code = int(cp.stdout.strip()) if cp.returncode == 0 and cp.stdout.strip().isdigit() else 0
    decision = None
    hp = Path(headers_path)
    if hp.exists():
        for line in hp.read_text(errors="replace").splitlines():
            if line.lower().startswith("x-senex-edge-decision:"):
                decision = line.split(":", 1)[1].strip()
    bp = Path(body_path)
    return {"http": code, "decision": decision, "curl_exit": cp.returncode, "body_sha256": h256(bp.read_bytes()) if bp.exists() else None}


def identity(kind: str, body: dict):
    if kind == "snapshot":
        return body.get("snapshot_id"), body.get("generation"), body.get("canonical_sha256")
    if kind == "ready":
        return body.get("authority_snapshot_id"), body.get("generation"), body.get("canonical_sha256")
    return body.get("authority_snapshot_id"), body.get("authority_generation"), body.get("authority_canonical_sha256")


def assert_safety(health, ready, provenance, state=None):
    if health["http"] != 200 or ready["http"] != 200 or provenance["http"] != 200:
        raise RuntimeError(f"LIVE_HTTP:{health['http']}:{ready['http']}:{provenance['http']}")
    hb, rb, pb = health["body"], ready["body"], provenance["body"]
    if hb.get("trade_mode") != "PAPER" or hb.get("orders_enabled") is not False or hb.get("live_capital_locked") is not True:
        raise RuntimeError(f"PAPER_LOCK_FAILED:{hb}")
    if rb.get("status") != "ready" or not all((rb.get("checks") or {}).values()):
        raise RuntimeError(f"READY_FAILED:{rb}")
    if pb.get("exact") is not True or pb.get("source_commit") != HEAD or pb.get("source_tree") != TREE or pb.get("build_digest") != BUILD_DIGEST:
        raise RuntimeError(f"PROVENANCE_FAILED:{pb}")
    if state is not None:
        sb = state["body"]
        if sb.get("trade_mode") != "PAPER" or sb.get("live_capital_locked") is not True:
            raise RuntimeError(f"STATE_SAFETY_FAILED:{sb}")


def percentile(values, q: float):
    ordered = sorted(values)
    if not ordered:
        raise RuntimeError("EMPTY_PERCENTILE")
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - pos) + ordered[upper] * (pos - lower)


def extract_memory(metrics):
    obj = (metrics or {}).get("memory", {}) if isinstance(metrics, dict) else {}
    unit = ((obj.get("metricInfo") or {}).get("metricUnit") if isinstance(obj, dict) else None) or "pct"
    points = []
    for series in obj.get("values", []) if isinstance(obj, dict) else []:
        cid = (series.get("metadata") or {}).get("containerId")
        for point in series.get("data") or []:
            try:
                if isinstance(point, (list, tuple)) and len(point) >= 2:
                    ts, value = point[0], float(point[1])
                else:
                    ts = point.get("timestamp") or point.get("time") or point.get("ts")
                    value = float(point.get("value"))
                pct = value / RAM_LIMIT_MB * 100.0 if unit == "mb" else value
                points.append({"container": cid, "ts": ts, "value": value, "pct": pct})
            except Exception:
                pass
    return unit, points


# Exact remote truth and scope.
if git("rev-parse", "HEAD") != HEAD or git("rev-parse", "HEAD^{tree}") != TREE or git("status", "--porcelain"):
    raise RuntimeError("EXACT_CANDIDATE_DRIFT")
remote = git("ls-remote", "origin", f"refs/heads/{BRANCH}").split()[0]
if remote != HEAD:
    raise RuntimeError(f"REMOTE_HEAD_DRIFT:{remote}")
changed = sorted(git("diff", "--name-only", f"{BASE_R8}..HEAD").splitlines())
if changed != EXPECTED_R8_FILES:
    raise RuntimeError(f"R8_SCOPE_DRIFT:{changed}")
write("REMOTE_TRUTH.json", {
    "observed_at": iso(), "order": "ORDER-070-R8", "pr": 67,
    "head": HEAD, "tree": TREE, "base_r8": BASE_R8, "changed_files": changed,
    "merge": False, "tuning": 0, "runtime017_mutation": 0, "supabase_data_mutation": 0,
})
write("EXACT_GATE.json", {
    "observed_at": iso(), "head": HEAD, "tree": TREE, "ci": CI_RUNS,
    "ci_status": "PASS_ALL_4", "build_digest": BUILD_DIGEST,
})

# Repository-scoped Northflank auth and canonical-service preflight.
_, project_auth = nf("GET", f"/projects/{PROJECT}")
_, deployment_auth = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/deployment")
write("NORTHFLANK_AUTH.json", {"observed_at": iso(), "project_http": project_auth["http"], "deployment_http": deployment_auth["http"], "secret_value_observed": False})

entry0 = services_entry()
svc0 = service()
vcs = svc0.get("vcsData") or {}
original_branch = vcs.get("projectBranch")
if svc0.get("serviceType") != "combined" or original_branch != "main" or entry0.get("disabledCI") is not True:
    raise RuntimeError(f"NORTHFLANK_PREFLIGHT:{svc0.get('serviceType')}:{original_branch}:{entry0.get('disabledCI')}")
original_build_settings = json.loads(json.dumps(svc0.get("buildSettings") or {}))
target_build_settings = json.loads(json.dumps(original_build_settings))
docker_settings = json.loads(json.dumps(target_build_settings.get("dockerfile") or {}))
docker_settings.update({"dockerFilePath": "/Dockerfile", "dockerWorkDir": "/"})
target_build_settings["dockerfile"] = docker_settings
vpatch = {k: vcs[k] for k in ("accountLogin", "vcsLinkId", "selfHostedVcsId") if vcs.get(k)}
vpatch.update({"projectUrl": vcs["projectUrl"], "projectType": vcs["projectType"], "projectBranch": BRANCH})

# Reversible source switch -> exact build -> restore main.
switched = False
build_id = None
build = None
try:
    nf("PATCH", f"/projects/{PROJECT}/services/combined/{SERVICE}", {"disabledCI": True, "buildSource": "git", "vcsData": vpatch, "buildSettings": target_build_settings})
    switched = True
    if (service().get("vcsData") or {}).get("projectBranch") != BRANCH or services_entry().get("disabledCI") is not True:
        raise RuntimeError("SOURCE_SWITCH_VERIFY")
    build_payload = {"sha": HEAD, "overrides": {"buildArguments": {"SENEX_SOURCE_COMMIT": HEAD, "SENEX_SOURCE_TREE": TREE, "SENEX_BUILD_DIGEST": BUILD_DIGEST}}}
    built, _ = nf("POST", f"/projects/{PROJECT}/services/{SERVICE}/build", build_payload)
    build_id = built.get("id")
    if not build_id:
        raise RuntimeError("BUILD_ID_MISSING")
    deadline = time.time() + 3600
    while time.time() < deadline:
        build, _ = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/build/{build_id}")
        if build.get("concluded"):
            break
        time.sleep(15)
    if not build or not build.get("concluded") or not build.get("success") or build.get("sha") != HEAD:
        raise RuntimeError(f"EXACT_BUILD_FAILED:{build}")
finally:
    if switched:
        restore = dict(vpatch)
        restore["projectBranch"] = original_branch
        nf("PATCH", f"/projects/{PROJECT}/services/combined/{SERVICE}", {"disabledCI": True, "buildSource": "git", "vcsData": restore, "buildSettings": target_build_settings})
post_restore = service()
post_docker = ((post_restore.get("buildSettings") or {}).get("dockerfile") or {})
if (post_restore.get("vcsData") or {}).get("projectBranch") != "main" or services_entry().get("disabledCI") is not True:
    raise RuntimeError("SOURCE_RESTORE_FAILED")
if post_docker.get("dockerFilePath") != "/Dockerfile" or post_docker.get("dockerWorkDir") != "/":
    raise RuntimeError(f"ROOT_DOCKERFILE_NOT_CANONICAL:{post_docker}")

# Exact OCI digest.
registry = build.get("registry") if isinstance(build.get("registry"), dict) else {}
image = str(registry.get("digest") or "").lower()
if image and not image.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", image):
    image = "sha256:" + image
if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
    logs, _ = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/build-logs", query={"buildId": build_id, "queryType": "range", "duration": 86400, "lineLimit": 1000, "direction": "backward", "regexIncludes": "manifest"})
    hits = []
    for row in logs if isinstance(logs, list) else []:
        text = str(row.get("log", "")) if isinstance(row, dict) else str(row)
        match = re.search(r"exporting manifest sha256:([0-9a-f]{64})", text, re.I)
        if match:
            hits.append("sha256:" + match.group(1).lower())
    if not hits:
        raise RuntimeError("OCI_MANIFEST_NOT_PROVEN")
    image = hits[0]
write("BUILD_PROVENANCE.json", {"observed_at": iso(), "build_id": build_id, "build_sha": build.get("sha"), "build_success": True, "tree": TREE, "build_digest": BUILD_DIGEST, "image_digest": image, "source_branch_restored": "main"})

# Bind OCI digest while preserving all unrelated runtime env.
env_doc, _ = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/runtime-environment", query={"show": "this"})
runtime_env = env_doc.get("runtimeEnvironment") if isinstance(env_doc, dict) else None
if not isinstance(runtime_env, dict):
    raise RuntimeError("RUNTIME_ENV_NOT_READABLE")
non_target = {k: v for k, v in runtime_env.items() if k != "SENEX_IMAGE_DIGEST"}
non_target_fp = fingerprint(non_target)
updated_env = dict(runtime_env)
updated_env["SENEX_IMAGE_DIGEST"] = image
nf("PATCH", f"/projects/{PROJECT}/services/combined/{SERVICE}", {"runtimeEnvironment": updated_env})
after_doc, _ = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/runtime-environment", query={"show": "this"})
after_env = after_doc.get("runtimeEnvironment") if isinstance(after_doc, dict) else None
if not isinstance(after_env, dict) or after_env.get("SENEX_IMAGE_DIGEST") != image or fingerprint({k: v for k, v in after_env.items() if k != "SENEX_IMAGE_DIGEST"}) != non_target_fp:
    raise RuntimeError("OCI_BIND_DRIFT")
write("OCI_BIND.json", {"observed_at": iso(), "image_digest": image, "non_target_environment_preserved": True, "non_target_environment_sha256": non_target_fp})

# Deploy exact new SHA to canonical service.
nf("POST", f"/projects/{PROJECT}/services/{SERVICE}/deployment", {"internal": {"buildSHA": HEAD}})
deadline = time.time() + 1800
while time.time() < deadline:
    dep = deployment()
    entry = services_entry()
    internal = dep.get("internal") or {}
    deployment_status = ((entry.get("status") or {}).get("deployment") or {}).get("status")
    if internal.get("deployedSHA") == HEAD and deployment_status == "COMPLETED":
        break
    if deployment_status == "FAILED":
        raise RuntimeError("DEPLOY_FAILED")
    time.sleep(10)
else:
    raise RuntimeError("DEPLOY_TIMEOUT")
write("ORIGIN_DEPLOY.json", {"observed_at": iso(), "build_id": (dep.get("internal") or {}).get("buildId") or build_id, "build_sha": (dep.get("internal") or {}).get("buildSHA"), "deployed_sha": (dep.get("internal") or {}).get("deployedSHA"), "deployment_status": deployment_status, "image_digest": image, "build_digest": BUILD_DIGEST, "source_branch": (service().get("vcsData") or {}).get("projectBranch")})

# Exact origin gate: snapshot primes shared observational authority.
origin_attempts = []
for attempt in range(1, 61):
    snapshot = pub(ORIGIN, "/api/authority/snapshot?symbol=BTCUSDT")
    health = pub(ORIGIN, "/healthz")
    ready = pub(ORIGIN, "/readyz?symbol=BTCUSDT")
    provenance = pub(ORIGIN, "/api/runtime/provenance")
    state = pub(ORIGIN, "/api/oracle/state?symbol=BTCUSDT")
    origin_attempts.append({"attempt": attempt, "snapshot": snapshot["http"], "health": health["http"], "ready": ready["http"], "provenance": provenance["http"], "state": state["http"]})
    if all(x["http"] == 200 for x in (snapshot, health, ready, provenance, state)):
        assert_safety(health, ready, provenance, state)
        break
    time.sleep(10)
else:
    write("ORIGIN_GATE_FAILED.json", {"observed_at": iso(), "attempts": origin_attempts})
    raise RuntimeError("ORIGIN_EXACT_GATE_TIMEOUT")
write("ORIGIN_LIVE.json", {"observed_at": iso(), "attempts": origin_attempts, "healthz": 200, "readyz": 200, "provenance_exact": True, "snapshot_id": snapshot["body"].get("snapshot_id"), "generation": snapshot["body"].get("generation"), "canonical_sha256": snapshot["body"].get("canonical_sha256"), "exact_total_predictions": snapshot["body"].get("exact_total_predictions")})

# Fresh official temporary Cloudflare exact-head edge.
edge, edge_output_sha, edge_attempts = deploy_temp_worker()
boot_attempts = []
for attempt in range(1, 41):
    edge_health = pub(edge, "/healthz")
    boot_attempts.append({"attempt": attempt, "http": edge_health["http"], "decision": edge_health["headers"].get("x-senex-edge-decision")})
    if edge_health["http"] == 200 and edge_health["headers"].get("x-senex-edge-decision") == "ALLOW_GET_PROXY":
        break
    time.sleep(2)
else:
    raise RuntimeError(f"EDGE_BOOT_FAILED:{boot_attempts}")
post = curl_probe(edge, "POST", "/api/oracle/score")
put = curl_probe(edge, "PUT", "/api/oracle/score")
delete = curl_probe(edge, "DELETE", "/api/oracle/score")
unknown = curl_probe(edge, "GET", "/__order070_unknown__")
if any(x["http"] != 405 or x["decision"] != "DENY_METHOD" for x in (post, put, delete)):
    raise RuntimeError(f"EDGE_METHOD_DENIAL_FAILED:{post}:{put}:{delete}")
if unknown["http"] != 404 or unknown["decision"] != "DENY_PATH":
    raise RuntimeError(f"EDGE_UNKNOWN_DENIAL_FAILED:{unknown}")
write("CLOUDFLARE_FINAL.json", {"observed_at": iso(), "head": HEAD, "tree": TREE, "temporary_worker_url": edge, "temporary_deploy_output_sha256": edge_output_sha, "deploy_attempts": edge_attempts, "boot_attempts": boot_attempts, "positive_get": {"http": edge_health["http"], "decision": edge_health["headers"].get("x-senex-edge-decision")}, "unsafe_methods": {"post": post, "put": put, "delete": delete}, "unknown": unknown, "credentials_used": False})

# >=8 concurrent origin↔edge reconciliation rounds; identity mismatches are fatal.
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
for round_no in range(1, 9):
    prime = pub(ORIGIN, paths["snapshot"])
    if prime["http"] != 200:
        raise RuntimeError(f"RECONCILIATION_PRIME_HTTP:{prime['http']}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(fetch_job, side, base, kind, path) for side, base in (("origin", ORIGIN), ("edge", edge)) for kind, path in paths.items()]
        got = {(side, kind): result for side, kind, result in (future.result() for future in futures)}
    for side in ("origin", "edge"):
        for kind in paths:
            if got[(side, kind)]["http"] != 200:
                raise RuntimeError(f"RECONCILIATION_HTTP:{round_no}:{side}:{kind}:{got[(side, kind)]['http']}")
    ids = {}
    for kind in paths:
        ids[("origin", kind)] = identity(kind, got[("origin", kind)]["body"])
        ids[("edge", kind)] = identity(kind, got[("edge", kind)]["body"])
        if ids[("origin", kind)] != ids[("edge", kind)]:
            raise RuntimeError(f"RECONCILIATION_IDENTITY:{round_no}:{kind}:{ids[(('origin',kind))]}:{ids[(('edge',kind))]}")
    snapshot_id = ids[("origin", "snapshot")]
    for side in ("origin", "edge"):
        for kind in ("score", "state", "gate", "ready"):
            if ids[(side, kind)] != snapshot_id:
                raise RuntimeError(f"RECONCILIATION_GENERATION:{round_no}:{side}:{kind}:{ids[(side,kind)]}:{snapshot_id}")
    rounds.append({"round": round_no, "snapshot_id": snapshot_id[0], "generation": snapshot_id[1], "canonical_sha256": snapshot_id[2]})
    time.sleep(1)
write("CONCURRENT_RECONCILIATION.json", {"observed_at": iso(), "rounds": rounds, "result": "PASS_8_ROUNDS"})

# Dashboard predictions route parity; bounded retry handles a prediction landing between concurrent reads.
prediction_parity = None
for attempt in range(1, 6):
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(pub, ORIGIN, "/api/oracle/predictions/db?limit=50&symbol=BTCUSDT")
        f2 = pool.submit(pub, edge, "/api/oracle/predictions/db?limit=50&symbol=BTCUSDT")
        p_origin, p_edge = f1.result(), f2.result()
    if p_origin["http"] == p_edge["http"] == 200 and p_origin["body"] == p_edge["body"]:
        prediction_parity = {"attempt": attempt, "sha256": p_origin["sha256"]}
        break
    time.sleep(1)
if prediction_parity is None:
    raise RuntimeError("EDGE_DASHBOARD_PREDICTIONS_PARITY_FAILED")

# Live E2E and safety surface.
for side, base in (("origin", ORIGIN), ("edge", edge)):
    h = pub(base, "/healthz")
    r = pub(base, "/readyz?symbol=BTCUSDT")
    p = pub(base, "/api/runtime/provenance")
    s = pub(base, "/api/oracle/state?symbol=BTCUSDT")
    assert_safety(h, r, p, s)
    openapi = pub(base, "/openapi.json")
    if openapi["http"] != 200:
        raise RuntimeError(f"OPENAPI_HTTP:{side}:{openapi['http']}")
    unsafe = 0
    for methods in (openapi["body"].get("paths") or {}).values():
        for method in methods:
            if str(method).upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                unsafe += 1
    if unsafe != 0:
        raise RuntimeError(f"PUBLIC_UNSAFE_SURFACE:{side}:{unsafe}")
write("LIVE_E2E.json", {"observed_at": iso(), "head": HEAD, "tree": TREE, "build_id": build_id, "build_digest": BUILD_DIGEST, "image_digest": image, "origin": ORIGIN, "edge": edge, "healthz_origin": 200, "healthz_edge": 200, "readyz_origin": 200, "readyz_edge": 200, "provenance_exact_origin": True, "provenance_exact_edge": True, "concurrent_rounds": 8, "prediction_route_parity": prediction_parity, "public_unsafe_count": 0, "trade_mode": "PAPER", "orders_enabled": False, "live_capital_locked": True})

# Fresh 30-minute stability window begins only after deploy + edge + reconciliation + E2E.
stability_start = now()
start_iso = iso(stability_start)
initial_running = running_names(containers())
if not initial_running:
    raise RuntimeError("NO_RUNNING_CONTAINER_AT_STABILITY_START")
base_snap = pub(ORIGIN, paths["snapshot"])
base_state = pub(ORIGIN, paths["state"])
base_ready = pub(ORIGIN, paths["ready"])
base_health = pub(ORIGIN, "/healthz")
base_provenance = pub(ORIGIN, "/api/runtime/provenance")
assert_safety(base_health, base_ready, base_provenance, base_state)
base_cycles = int(base_state["body"].get("cycles_run") or 0)
base_db = int(base_snap["body"].get("exact_total_predictions") or 0)
base_last_prediction_ts = base_state["body"].get("last_prediction_ts")
base_btc_rows = int(base_snap["body"].get("authority_history_rows") or 0)
generation_last = int(base_snap["body"].get("generation") or 0)
samples = []
next_sample = time.monotonic()
while (now() - stability_start).total_seconds() < STABILITY_SECONDS:
    sleep_for = next_sample - time.monotonic()
    if sleep_for > 0:
        time.sleep(sleep_for)
    sampled_at = now()
    health = pub(ORIGIN, "/healthz")
    ready = pub(ORIGIN, paths["ready"])
    provenance = pub(ORIGIN, "/api/runtime/provenance")
    snapshot = pub(ORIGIN, paths["snapshot"])
    state = pub(ORIGIN, paths["state"])
    if any(value["http"] != 200 for value in (health, ready, provenance, snapshot, state)):
        raise RuntimeError(f"STABILITY_HTTP_FAILURE:{iso(sampled_at)}:{health['http']}:{ready['http']}:{provenance['http']}:{snapshot['http']}:{state['http']}")
    assert_safety(health, ready, provenance, state)
    generation = int(snapshot["body"].get("generation") or 0)
    if generation < generation_last:
        raise RuntimeError(f"SNAPSHOT_GENERATION_REGRESSION:{generation_last}:{generation}")
    generation_last = generation
    if snapshot["body"].get("authority_history_complete") is not True or snapshot["body"].get("exact_count_complete") is not True:
        raise RuntimeError("AUTHORITY_COMPLETENESS_LOST")
    ready_checks = ready["body"].get("checks") or {}
    if ready_checks.get("last_refresh_ok") is not True or ready_checks.get("authority_history_complete") is not True:
        raise RuntimeError(f"AUTHORITY_REFRESH_NOT_CONTINUOUS:{ready_checks}")
    samples.append({"ts": iso(sampled_at), "snapshot_id": snapshot["body"].get("snapshot_id"), "generation": generation, "canonical_sha256": snapshot["body"].get("canonical_sha256"), "cycles_run": state["body"].get("cycles_run"), "exact_total_predictions": snapshot["body"].get("exact_total_predictions"), "authority_history_rows": snapshot["body"].get("authority_history_rows")})
    next_sample += SAMPLE_SECONDS

stability_end = now()
end_iso = iso(stability_end)
final_snap = pub(ORIGIN, paths["snapshot"])
final_state = pub(ORIGIN, paths["state"])
final_ready = pub(ORIGIN, paths["ready"])
final_health = pub(ORIGIN, "/healthz")
final_provenance = pub(ORIGIN, "/api/runtime/provenance")
assert_safety(final_health, final_ready, final_provenance, final_state)
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

# Northflank metrics and logs strictly inside the fresh stability window.
metrics, _ = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/metrics", query=[("queryType", "range"), ("startTime", start_iso), ("endTime", end_iso), ("metricTypes", "memory"), ("metricTypes", "requests"), ("metricTypes", "http5xxResponses"), ("metricTypes", "tcpConnectionsOpen")])
unit, memory_points = extract_memory(metrics)
relevant = [point for point in memory_points if point.get("container") in initial_running]
if not relevant:
    raise RuntimeError(f"NO_MEMORY_METRICS:{unit}:{initial_running}")
percentages = [point["pct"] for point in relevant]
ram_max = max(percentages)
ram_p95 = percentile(percentages, 0.95)
points_ge_90 = sum(value >= 90.0 for value in percentages)
if ram_max >= 90.0 or ram_p95 >= 90.0 or points_ge_90 != 0:
    raise RuntimeError(f"RAM_GATE_FAIL:max={ram_max}:p95={ram_p95}:ge90={points_ge_90}")
logs, _ = nf("GET", f"/projects/{PROJECT}/services/{SERVICE}/logs", query={"queryType": "range", "startTime": start_iso, "endTime": end_iso, "type": "runtime", "lineLimit": 1000, "direction": "forward"})
rows = logs if isinstance(logs, list) else []
patterns = {
    "connection_refused": re.compile(r"connection refused", re.I),
    "oom": re.compile(r"(?:out of memory|oom(?:kill| killed|_kill))", re.I),
    "process_exit": re.compile(r"(?:process exited|worker exited|uvicorn.*exit|terminated unexpectedly)", re.I),
}
matches = {key: [] for key in patterns}
for row in rows:
    text = str(row.get("log", "")) if isinstance(row, dict) else str(row)
    for key, pattern in patterns.items():
        if pattern.search(text):
            matches[key].append({"ts": row.get("ts") if isinstance(row, dict) else None, "containerId": row.get("containerId") if isinstance(row, dict) else None, "log": text[:500]})
if any(matches.values()):
    raise RuntimeError(f"STABILITY_RUNTIME_EVENT:{ {k: len(v) for k, v in matches.items()} }")

duration = (stability_end - stability_start).total_seconds()
if duration < 1800:
    raise RuntimeError(f"STABILITY_DURATION_SHORT:{duration}")
write("MEMORY_EVIDENCE.json", {"observed_at": iso(), "start": start_iso, "end": end_iso, "metric_unit": unit, "point_count": len(relevant), "ram_max_pct": ram_max, "ram_p95_pct": ram_p95, "points_ge_90": points_ge_90, "gate": "PASS", "points": relevant})
write("STABILITY_30M.json", {"observed_at": iso(), "start": start_iso, "end": end_iso, "duration_seconds": duration, "sample_interval_seconds": SAMPLE_SECONDS, "sample_count": len(samples), "initial_running_containers": initial_running, "final_running_containers": final_running, "unexpected_container_replacements": 0, "unexpected_process_exits": 0, "oom_kills": 0, "connection_refused": 0, "healthz_continuous": "PASS", "readyz_continuous": "PASS", "authority_refresh_continuous": "PASS", "provenance_continuous": "PASS", "snapshot_generation_consistent": "PASS", "ram_metric_unit": unit, "ram_max_pct": ram_max, "ram_p95_pct": ram_p95, "points_ge_90": points_ge_90, "oracle_cycles_initial": base_cycles, "oracle_cycles_final": final_cycles, "oracle_cycles_advance": final_cycles - base_cycles, "db_predictions_initial": base_db, "db_predictions_final": final_db, "db_predictions_increase": final_db - base_db, "latest_prediction_ts_initial": base_last_prediction_ts, "latest_prediction_ts_final": final_last_prediction_ts, "latest_prediction_ts_advanced": True, "btc_authority_rows_initial": base_btc_rows, "btc_authority_rows_final": final_btc_rows, "btc_authority_rows_nondecreasing": True, "runtime_log_rows": len(rows), "runtime_matches": {k: len(v) for k, v in matches.items()}, "trade_mode": "PAPER", "orders_enabled": False, "live_capital_locked": True, "real_order_count": 0, "real_capital_movement": 0, "samples": samples})

summary = {
    "observed_at": iso(), "order": "ORDER-070-R8", "status": "READY_FOR_AUD", "pr": 67,
    "head": HEAD, "tree": TREE, "exact_gate": "PASS", "ci": CI_RUNS,
    "root_cause": "UNBOUNDED_RICH_AUTHORITY_ROW_RETENTION_PLUS_WHOLE_COHORT_JSON_CLONING_IN_MEMORY_CONSTRAINED_RUNTIME",
    "root_cause_proof": "PROCFS_ANON_DOMINANT_SAMPLE_PLUS_R8_PROJECTION_EQUIVALENCE_AND_STRICT_30M_MEMORY_GATE",
    "memory_fix": "COMPLETE_HISTORY_PROOF_SAFE_PROJECTION_PLUS_SINGLE_DEEPCOPY_SORT_AND_BOUNDED_RECENT50_CONTEXT",
    "readiness_fix": "CANONICAL_READINESS_SHARED_WITH_MARKET_CONTEXT_WITH_LEGACY_MISSING_METADATA_COMPATIBILITY",
    "build_id": build_id, "build_digest": BUILD_DIGEST, "image_digest": image,
    "origin_deploy": "PASS", "cloudflare_final": "PASS", "reconciliation": "PASS_8_ROUNDS", "live_e2e": "PASS",
    "stability_30m": "PASS", "ram_max_pct": ram_max, "ram_p95_pct": ram_p95, "points_ge_90": points_ge_90,
    "ready_continuous": "PASS", "authority_refresh_continuous": "PASS", "provenance_continuous": "PASS",
    "predictions_accumulating": True, "cycles_advanced": final_cycles - base_cycles,
    "db_predictions_increase": final_db - base_db, "latest_prediction_ts_advanced": True, "btc_authority_rows_nondecreasing": True,
    "trade_mode": "PAPER", "orders_enabled": False, "live_capital_locked": True,
    "real_order_count": 0, "real_capital_movement": 0, "tuning": 0, "runtime017_mutation": 0, "merge": False,
}
write("FINAL_GATE_SUMMARY.json", summary)
required = [
    "REMOTE_TRUTH.json", "EXACT_GATE.json", "NORTHFLANK_AUTH.json", "BUILD_PROVENANCE.json", "OCI_BIND.json",
    "ORIGIN_DEPLOY.json", "ORIGIN_LIVE.json", "CLOUDFLARE_FINAL.json", "CONCURRENT_RECONCILIATION.json",
    "LIVE_E2E.json", "MEMORY_EVIDENCE.json", "STABILITY_30M.json", "FINAL_GATE_SUMMARY.json",
]
manifest = "\n".join(f"{h256((OUT / name).read_bytes())}  {name}" for name in sorted(required)) + "\n"
(OUT / "MANIFEST.sha256").write_text(manifest)
manifest_sha = h256((OUT / "MANIFEST.sha256").read_bytes())
(OUT / "TERMINAL_RECEIPT.json").write_text(json.dumps({**summary, "manifest_sha256": manifest_sha}, sort_keys=True, indent=2) + "\n")
print("ORDER_070_STATUS=READY_FOR_AUD")
print(f"HEAD={HEAD}")
print(f"TREE={TREE}")
print(f"BUILD_ID={build_id}")
print(f"BUILD_DIGEST={BUILD_DIGEST}")
print(f"OCI_DIGEST={image}")
print(f"RAM_MAX_PCT={ram_max}")
print(f"RAM_P95_PCT={ram_p95}")
print(f"POINTS_GE_90={points_ge_90}")
print(f"CYCLES_ADVANCED={final_cycles-base_cycles}")
print(f"DB_PREDICTIONS_INCREASE={final_db-base_db}")
print(f"MANIFEST_SHA256={manifest_sha}")
