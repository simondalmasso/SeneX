# ORDER086 Decision MCP evidence

Updated: 2026-09-27
Branch: `order086/gptrader-paper-mcp`

This evidence is fail-closed. It does not claim runtime persistence, schedule readiness, merge readiness, or live capability.

## Safety

- PAPER_ONLY=true
- SIMULATION_ONLY=true
- LIVE=NO
- REAL_ORDERS=0
- CAPITAL=0
- DIRECT_GPTRADER_D1_READS=0
- DIRECT_GPTRADER_D1_WRITES=0
- Existing `GPTrader Hourly PAPER` remains disconnected.
- H011 remains restored to canonical c7 and is outside this branch's deployment path.
- H011 auto-deploy remains disarmed.
- MCP runtime activation remains frozen pending independent exact-head clearance.

## Incident quarantine

Canonical incident manifest:

`research/gptrader/deployment/ORDER086_INCIDENT_QUARANTINE.json`

Quarantined prediction IDs:

`6868, 6870, 6872`

Incident epoch:

`[2026-09-27T21:29:53Z, 2026-09-27T22:10:23Z)`

Rules:

- preserve incident rows/packets for forensics;
- do not delete or rewrite them;
- exclude the three prediction rows and any derived settlement/science contribution from canonical evaluation;
- quarantine any sealed T0 packet whose packet/payload timestamp falls inside the incident epoch;
- incident PAPER mutations independently observed: 0.

## Decision-log durability and migration

The durable decision log now enforces:

- only a genuinely torn, non-newline final JSON fragment may be auto-truncated;
- malformed newline-terminated or non-tail corruption fails closed and writes an explicit local quarantine marker;
- identical legacy duplicates are logically deduplicated by `(policy_id, packet_id)`;
- first durable occurrence remains authoritative for provenance;
- conflicting duplicates fail closed and surface through Decision MCP health;
- public/science logical counts use first-occurrence dedupe;
- corrupt cursor is distinct from missing cursor and makes Decision MCP health not-ready;
- missing `paper_state.json` with durable decisions fails closed;
- a durable decision history with no applied-decision ledger fails closed.

## Runtime ownership invariant

Hard deployment invariant:

- instances=1
- autoscaling=off
- uvicorn workers=1
- one GPTrader state root per runtime
- startup must hold the exclusive OS-level GPTrader root lease
- a second process sharing the same root must refuse startup

Required runtime command:

```text
uvicorn backend.gptrader.mcp_http:create_app_from_env --factory --host 0.0.0.0 --port 8080 --workers 1 --no-access-log
```

The runtime factory owns the root lease for the lifetime of the FastAPI application. This is the code enforcement behind the one-instance/one-worker contract.

## Persistence claim boundary

Local filesystem durability has been hardened:

- decision/log appends flush + `fsync`;
- atomic JSON replacement flushes the file and fsyncs the parent directory on POSIX;
- sealed packet first-create and checkpoint replacement fsync the parent directory on POSIX;
- TradeJournal appends flush + `fsync`, with parent-directory fsync on first create on POSIX.

This does not prove provider-level power-loss durability. A provider may still have storage/cache semantics outside the process's control.

Therefore:

```text
N7_DURABILITY_CLAIM=BOUNDED_LOCAL_POSIX_FSYNC_ONLY
PERSISTENCE=NO
```

`PERSISTENCE` may become PASS only after the accepted exact SHA runs against the intended persistent volume and survives a deliberate restart with unchanged cursor, decisions, PAPER state, packet log, and logical counts.

## H2 transport

H2 remains the selected topology:

- sole canonical SENEX producer;
- authenticated `POST /ingest/t0`;
- packet id/hash/seq revalidation;
- identical resend no-op;
- conflicts, gaps, bad hash, oversize, and future/outcome contamination fail closed;
- consumer durable before ACK;
- producer cursor advances only after exact ACK;
- no D1/current-price/outcome lookup.

No H2 activation is authorized while MCP is frozen.

## Decision MCP tool boundary

Exactly four MCP tools:

1. `get_gptrader_health`
2. `get_prediction_batch`
3. `get_gptrader_state`
4. `submit_paper_decisions`

`/ingest/t0` is not an MCP tool.

Authenticated `/mcp` raw request body is bounded before JSON decode. The ingest body is also streaming-bounded before JSON decode.

Forbidden runtime capabilities/credentials:

- D1/Supabase credentials
- exchange credentials
- wallet credentials
- broker credentials
- signer/private keys
- arbitrary shell dispatch

## Current activation status

```text
MCP_RUNTIME=FROZEN
PERSISTENCE=NO
TASK_CONNECTED=NO
READY_FOR_SCHEDULE=NO
MERGE=BLOCKED
```

Manual exact-SHA deployment may only reopen after exact-head canonical CI PASS plus independent ORDER087/GLM and ARENA clearance. Any later deployment must read back the exact deployed SHA and preserve the single-instance/single-worker invariant.
