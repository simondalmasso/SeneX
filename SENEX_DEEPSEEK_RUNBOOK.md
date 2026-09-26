# SENEX_DEEPSEEK_RUNBOOK

Short, executable. All commands run from the repository root (the directory
containing `Dockerfile`, `senecio_polymarket/`, `tests/`).

## Environment

```bash
python3 -m venv .venv
.venv/bin/pip install fastapi==0.128.0 uvicorn==0.44.0 httpx==0.28.1 \
  numpy==1.26.4 ccxt==4.5.58 websockets==16.0 sse-starlette==3.3.4 pytest
```

Runtime env (sandbox/local only; the container gets real values from the
provider):

```bash
export SUPABASE_URL="http://127.0.0.1:9"        # intentionally unreachable -> authority fail-closed
export SUPABASE_KEY="sandbox-fake-not-a-secret" # never a real secret in sandbox
export SENEX_RUNTIME_ROOT="$PWD/senecio_polymarket"
```

## Tests

```bash
PYTHONPATH=$PWD/senecio_polymarket .venv/bin/python -m pytest tests/ -q
# expected: 65 passed (includes the Linux symlink case that Windows skips)
```

## Compile check

```bash
.venv/bin/python -m compileall -q senecio_polymarket && echo OK
find . -name __pycache__ -not -path './.git/*' -exec rm -rf {} +
```

## Bake artifact identity (replicates the Dockerfile RUN steps; sandbox)

```bash
cd senecio_polymarket
PYTHONPATH=$PWD ../.venv/bin/python backend/artifact_identity.py write \
  --root "$PWD" \
  --output "$PWD/.senex-provenance/artifact-identity.json" \
  --source-commit "$(git -C .. rev-parse HEAD)" \
  --source-tree "$(PYTHONPATH=$PWD ../.venv/bin/python -c \
    "from pathlib import Path; from backend.artifact_identity import git_tree_sha; print(git_tree_sha(Path('..')))")"
```

Readback / exactness inspection:

```bash
PYTHONPATH=$PWD ../.venv/bin/python -c "
from pathlib import Path
from backend.artifact_identity import read_artifact_identity
r = read_artifact_identity(Path('.senex-provenance/artifact-identity.json'), root=Path('.'))
print('exact=', r['exact'], 'checks=', r['checks'])
print('declared=', r['declared_build_digest'])
print('computed=', r['computed_build_digest'])
"
```

## Local run (no container)

```bash
cd senecio_polymarket
SUPABASE_URL="$SUPABASE_URL" SUPABASE_KEY="$SUPABASE_KEY" \
SENEX_RUNTIME_ROOT="$PWD" PYTHONPATH="$PWD" \
../.venv/bin/python -m uvicorn backend.main_real:app --host 127.0.0.1 --port 8080
```

The container entrypoint (`start_single_authority.sh`) additionally runs the
settlement reconciler guard and requires a real Supabase; it is the PROVIDER
path, not the sandbox path.

## Health probe

```bash
curl -s http://127.0.0.1:8080/healthz | python3 -m json.tool
# 200 {"status":"alive","safety":{...,"hard_paper_lock":true},"provenance":{"exact":true,...}}
```

## Readiness probe

```bash
curl -s -w '\nHTTP %{http_code}\n' http://127.0.0.1:8080/readyz
# without Supabase authority: 503 {"status":"not_ready","reason":"NO_VALID_AUTHORITY_GENERATION",...}
# (fail-closed is CORRECT; do not "fix" it)
```

## Paper smoke surfaces

```bash
curl -s http://127.0.0.1:8080/api/paper/state  | python3 -m json.tool
curl -s http://127.0.0.1:8080/api/paper/trades?limit=20 | python3 -m json.tool
curl -s http://127.0.0.1:8080/api/market-context | python3 -m json.tool   # requires authority (503 without)
```

## Dashboard

Open `http://127.0.0.1:8080/` in a browser (served from `frontend/index.html`).
Panels: Authoritative score · Oracle predictions · Polymarket prior + CLOB ·
Kalshi/Boros diagnostics · Runtime & safety · **Paper execution
(HYPOTHETICAL)** · **Model quality (EDGE UNPROVEN)** · Learning loop · Source
integrity. UNKNOWN is rendered as UNKNOWN, never zero.

## Candidate tree calculation (true Git semantics)

```bash
PYTHONPATH=$PWD/senecio_polymarket .venv/bin/python -c "
from pathlib import Path
from backend.artifact_identity import git_tree_sha
print(git_tree_sha(Path('.')))"
# must equal: git rev-parse HEAD^{tree}   (validated in this sandbox)
```

## Docker build (external — no container builder exists in this sandbox)

```bash
docker build --build-arg NF_GIT_SHA=<actual-final-gitlab-commit> -t senex-b81:<tag> .
docker run --rm -p 8080:8080 \
  -e SUPABASE_URL=<real> -e SUPABASE_KEY=<real> senex-b81:<tag>
```

---

## EXTERNAL AUDITOR / PROVIDER NEXT STEPS

1. Independently audit `SENEX_DEEPSEEK_FINAL.patch` (exact unified diff vs the
   supplied SOURCE/ input; note the input was a Windows CRLF worktree zip —
   the candidate normalizes to LF, which is REQUIRED for the shell entrypoint
   to execute on Linux).
2. Import the exact `FINAL_SOURCE/` into a new/private GitLab branch
   (senex-b8.1-provenance-only continuation or controlled successor).
3. Verify the resulting Git tree equals `CANDIDATE_CONTENT_TREE` from the
   final report (`git rev-parse <commit>^{tree}`).
4. Create the actual final Git commit (this is the only legitimate source of
   `FINAL_PROVIDER_COMMIT`).
5. Build that exact commit in isolated Northflank/Linux with
   `NF_GIT_SHA == actual commit`.
6. Prove the runtime `source_tree == final Git tree` (stage-1 baked value).
7. Prove `declared_build_digest == computed_build_digest` and `exact=true`
   on `/healthz` provenance.
8. Obtain the NEW OCI provider attestation (old digests from B8 are OLD
   EVIDENCE and must not be reproduced by a modified candidate).
9. Only then consider controlled external cutover (owner gates G1–G6 per the
   architecture council still apply; science gate is currently terminal-RED).
10. Run the actual 24–48h PAPER soak (the sandbox smoke is a SMOKE_SAMPLE,
    never a soak).
11. Assess edge only after real resolved evidence windows (EDGE=UNPROVEN
    until then; the honest default is ABSTAIN — proven live: Kelly=0 without
    authority evidence produces zero paper orders).
