# ORDER086 Decision MCP deployment evidence

Updated: 2026-09-27
Branch: `order086/gptrader-paper-mcp`

This document records only verified preflight and code evidence. It does not claim that the Decision MCP is deployed or schedule-ready.

## Safety authority

- PAPER_ONLY=true
- SIMULATION_ONLY=true
- LIVE=NO
- REAL_ORDERS=0
- CAPITAL=0
- DIRECT_GP_TRADER_D1_READS=0
- DIRECT_GP_TRADER_D1_WRITES=0
- Existing GPTrader task must remain disconnected until runtime smoke, legitimate producer proof, persistence proof, and ORDER087 clearance all pass.

## GitHub code state

- Parent H2 implementation SHA: `2f5d5e6000ab589306225e1aae7a9fb90e0470ae`
- Parent canonical CI: `36301491015` PASS, full product regression 203 passed, Docker build PASS, compileall PASS.
- Parent mirror: `36301489106` PASS.
- The ORDER086 forward-fix series adds an HTTP streaming body bound for `/ingest/t0`; exact-head CI/mirror must pass before deployment.
- PR #76 remains DRAFT and unmerged.

## Northflank preflight

- Team: `simondalmassos-team`
- Project: `seneciobot`
- Authenticated team-scoped Northflank API/CLI context: PASS.
- Service-create permission `ps_services_general_create`: PASS.
- Existing services: `seneciobot`, `senecio-h011`.
- Target service `senex-gptrader-mcp`: NOT CREATED.
- Developer Sandbox documented service allowance: 2 services; the project already has 2.
- Billing usage/invoice endpoints for the current account return `403 Feature disabled for your account`.
- Smallest listed deployment plan: `nf-compute-10` (0.1 vCPU, 256 MiB), account-reported $0.004/hour and $2.70/month.
- Local Decision MCP startup/auth-boundary smoke: PASS.
- PaaS persistent-volume minimum: 6 GB.
- Preferred volume: `nvme`, `ReadWriteOnce`, 6144 MiB, mounted at `/app/polymarket/results`.
- Published disk rate: $0.15/GB/month; 6 GB adds $0.90/month.
- Verified steady-state infrastructure floor: $3.60/month for `nf-compute-10` + 6 GB persistent volume, before usage-dependent build/network charges.
- COST_POLICY=BLOCK_REAL_COST until owner explicitly authorizes Northflank cost.

## Producer topology

H1 shared storage is rejected for the existing H011 volume:

- Existing `h011-results-vol` is `ReadWriteOnce`.
- It is attached to `senecio-h011`.
- It must not be simultaneously attached to an unrelated Decision MCP workload.

H2 one-way T0 replication is the selected topology:

- Existing SENEX oracle/sealer remains the sole canonical packet producer.
- Consumer endpoint: authenticated `POST /ingest/t0`.
- Replicated packet id/hash/seq are revalidated by the consumer.
- Identical duplicate is a no-op.
- Conflicting id/hash, sequence gap, bad hash, oversized packet, and future/outcome contamination fail closed.
- Consumer journal/checkpoint are durable before acknowledgement.
- Producer replication cursor advances only after an exact acknowledgement.
- Request body is streaming-bounded before JSON decoding.
- Decision MCP tool surface remains exactly four tools; ingest is not an MCP tool.
- No D1/current-price/outcome lookup is added by the transport.

H2 code is not runtime-active yet. The current H011 deployment is sourced from legacy GitLab and would need a PAPER-only ORDER086 producer deployment before real packets can replicate. That deployment requires the separate authorization gate in ORDER086 if not otherwise authorized.

## Intended Decision MCP service

Source repository: `https://github.com/simondalmasso/SeneX`

Source branch: `order086/gptrader-paper-mcp`

Dockerfile: `/Dockerfile`

Workdir: `/`

Runtime command:

```text
uvicorn backend.gptrader.mcp_http:create_app_from_env --factory --host 0.0.0.0 --port 8080 --workers 1 --no-access-log
```

Runtime variables required for H2:

- `SENEX_GPTRADER_MCP_TOKEN` — secret, >=32 chars; never persist value.
- `SENEX_GPTRADER_INGEST_TOKEN` — separate producer-ingest secret, >=32 chars; never persist value.
- `SENEX_RESULTS_DIR=/app/polymarket/results`
- H011 producer additionally needs the ingest URL and ingest token only when activation is authorized.

Forbidden in Decision MCP runtime:

- Supabase/D1 credentials
- exchange credentials
- wallet credentials
- broker credentials
- private keys

Port: internal HTTP 8080. Public Decision MCP exposure, if created, must use Northflank HTTPS/TLS. Producer transport should use the narrowest authenticated route available; no arbitrary fetch endpoint is permitted.

## Runtime proof status

- MCP_SOURCE_SHA=NOT_DEPLOYED
- MCP_HTTPS=NOT_CREATED
- MCP_AUTH=NOT_PROVISIONED
- MCP_RUNTIME_SMOKE=NOT_RUN
- MCP_RESTART_PERSISTENCE=NOT_RUN
- SEALED_PACKET_PRODUCER=CODE_DEFINED_RUNTIME_NOT_PROVEN
- SEALED_PACKET_COUNT=NOT_PROVEN
- READY_FOR_SCHEDULE=NO
- EXISTING_GPTRADER_TASK_CONNECTED=NO
- No TAKE is required or permitted solely to test deployment.

## Rollback

Before connecting the existing GPTrader Hourly PAPER task, rollback is defined as:

1. Disable the existing GPTrader Hourly PAPER task.
2. Stop or pause the dedicated Decision MCP service.
3. Retain the persistent volume and deployment evidence.
4. Do not delete GPTrader journals.
5. Do not mutate SENEX control PAPER state.
6. Preserve logs and checkpoint evidence.
7. Restore the last verified source SHA if source rollback is needed.

Rollback is non-destructive.
