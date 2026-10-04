# ORDER075 RECOVERY BUNDLE — LAYOUT & PREFLIGHT (secret-free, read-only provenance)

## PROVENANCE
All included bytes re-fetched from canonical Google Drive (2026-08-29) and SHA256-verified
against the protected-surface claims (checkpoint ad5d4e8d / recovery_seal 938f8c75 / runtime
EVIDENCE self-report). No secret, token, or credential is included in this bundle.

## BYTE MANIFEST (this bundle)
- order075/shadow_daemon.py        5267facf92927280b515618e0ab93b2e324772f8514ecc69327890858ce0bb8c  (RUNTIME)
- order075/edge_lab.py             2c4ff2363412c56df77f507870e23ee2c0732a7e33b06b5371ea6f8072eb0aaf  (RUNTIME)
- order075/state/shadow_anchor.json ff853662431363ea07b0237d010c5af0885c1aec9618a76ece316c63c523a59a (IDENTITY, byte-exact)
- schema.sql.reference_only        4d5662760c420b6f2a7b998cc0803b969f8d012f16e201aae90ea7537675c475  (REFERENCE ONLY - not read at runtime)
- worker.js.reference_only         fc70f6b0e148f29dbeb823073feadad1b56f954fff01594eb751f6620cbcef42  (REFERENCE ONLY - deployed Worker already live)
- MISSING (owner must supply, verify sha): coverage_spec.json  bcfa24b960386cb8ea87e13bbf2881fc723f60a67251ad16adcebad5e1ad90ee
  (Drive bytes are behind Google sign-in wall; hash verified via runtime EVIDENCE self-report only)

## TARGET LAYOUT (PROVEN from shadow_daemon.py L19-L23)
shadow_daemon.py line 20: ROOT = Path(__file__).resolve().parent
=> ROOT is THE DIRECTORY CONTAINING shadow_daemon.py. All state is ROOT-relative:

/app/polymarket/results/order075/          <- ROOT (dir that contains shadow_daemon.py)
|-- shadow_daemon.py                        <- canonical bytes, unchanged
|-- edge_lab.py                             <- canonical bytes, unchanged (sibling; `from edge_lab import *`)
|-- private/
|   `-- ingest_token                        <- OWNER SUPPLIES (0600). Value MUST equal Cloudflare Worker
|                                              env.INGEST_TOKEN. Code reads it AT IMPORT (L23); a missing
|                                              file = immediate FileNotFoundError = process cannot start.
|                                              `private/` is NOT auto-created by the code.
|-- raw/                                    <- auto-created by code (L21); may pre-exist empty
`-- state/
    |-- shadow_anchor.json                  <- restore byte-exact from this bundle (code NEVER reads/writes
    |                                          it - zero references; identity artifact only)
    `-- (status.json)                       <- auto-written every 10s while running; do NOT pre-create

WARNING: if shadow_daemon.py is placed one level deeper, ROOT shifts and raw/state/private
move with it. Keep the three siblings adjacent to the script. No env vars are read (grep-proven:
zero os.environ/getenv matches); no absolute paths in code; no cwd dependency.

## RUNTIME DEPENDENCIES (complete, code-derived)
- python3 >= 3.10 (edge_lab.py L107 uses PEP 604 `int|None`)
- pip packages: aiohttp, websockets   (stdlib otherwise)
- outbound network only: wss://ws-subscriptions-clob.polymarket.com/ws/market,
  wss://ws-live-data.polymarket.com, wss://data-stream.binance.vision:443/stream?streams=...,
  https://gamma-api.polymarket.com, https://clob.polymarket.com,
  https://senex-order075-edge-lab.simondalmasso44.workers.dev/ingest (POST, Bearer TOKEN)
- no inbound ports, no DB driver (D1 reached ONLY via the Worker), no cron, no lockfile
  (single-daemon invariant must be enforced EXTERNALLY - e.g. `timeout` wrapper or equivalent)

## PREFLIGHT CHECKLIST (all read-only until START)
[ ] /app/polymarket/results is writable (rw mount) and has >= 1 GB free
[ ] python3 --version >= 3.10 inside target container; `python3 -c "import aiohttp, websockets"` OK
[ ] single-instance guarantee chosen (P0 used `timeout <secs>`; daemon itself has NO lockfile)
[ ] INGEST_TOKEN decision: recover existing value (only if /app/data/order075/private/ingest_token
    still exists on the live pod) OR owner rotates via `wrangler secret put INGEST_TOKEN` (the ONLY
    required config mutation; worker.js bytes stay unchanged) and writes the same value to
    private/ingest_token (0600)
[ ] supervisor policy decision: anchor says supervisor_max_hours=74 from 2026-08-28T09:31:41.113Z
    (deadline 2026-08-31T09:31:41Z) and required_hours=72. NOTE (arithmetic): 72h valid within the
    74h envelope is no longer achievable (5.10h valid accumulated; <36h wall left at audit time).
    Owner must decide: reduced-window SAME_ANCHOR evidence, new order/anchor, or stop.
[ ] coverage_spec.json supplied owner-side and sha256-verified before any future seal generation
[ ] after staging: sha256sum -c SHA256SUMS passes; /app/data/order075 (historical, false path) left untouched

## HISTORICAL FALSEHOOD (preserved, do not delete)
P0 checkpoint (ad5d4e8d...) claimed recovery.path=/app/data/order075 with
persistent_volume_path=true. FALSIFIED by owner pod evidence (virtiofs persistent mount is
/app/polymarket/results; 40234-file exhaustive search found 0 ORDER075 artifacts). The P0
checkpoint is historically false on that field and is preserved as evidence, not deleted.
