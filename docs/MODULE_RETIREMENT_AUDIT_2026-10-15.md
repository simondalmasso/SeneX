# SENEX module activity audit — 2026-10-15

## Decision summary

The audit separates a dormant decision engine from still-required traceability and safety components. It removes the GPTrader decision plane and offline-only NVIDIA/DeepSeek provider implementation from the candidate. It moves immutable T0 receipt sealing and optional receipt replication to `backend/t0_audit/`, retaining the legacy durable wire format and on-disk location to avoid corrupting or orphaning historical evidence.

## Inventory and disposition

| Module/surface | Evidence | Disposition |
| --- | --- | --- |
| GPTrader decision service, fixed-risk hypothetical book, policy/science/verdict logic | H011-local canary was stale; no TAKE sample; no separate active scheduler was documented | **Remove** from runtime candidate, tests and UI; remove its three public GET routes and optional MCP sidecar |
| NVIDIA NIM / DeepSeek V4.1 Flash decision profile | Marked PREPARED/OFFLINE-ONLY; network disabled by code; no provider calls permitted | **Remove** active profile implementation, runbook and provider tests; preserve historical research/evidence |
| T0 sealing and receipt outbox | Called by `oracle_runner` before its dedupe marker and downstream PAPER routing; used by lineage/recovery tests | **Keep and relocate** to `backend/t0_audit/`; do not discard the receipt pipeline |
| Optional T0 receipt replication | Used by `oracle_runner`; bounded HTTPS `/ingest/t0`, append-only cursor, no market/account/order lookup | **Keep**, rename preferred environment variables to `SENEX_T0_REPLICATION_URL/TOKEN`, accept old names as compatibility aliases |
| REA deterministic evaluator subset | Explicit UI → GET route → closed candles → Grid/DCA/Combo evaluator → PAPER metrics; no order executor | **Keep and test** as a read-only PAPER diagnostic |
| REA CLI and historical analysis tools | Offline dataset/hash-guarded research; intentionally excluded from the image except for pure deterministic evaluators | **Keep offline**; not an inactive production trading module |
| ORDER097/098/099/100 and challenger research | Explicit experimental protocols/artifacts; ORDER100 prospective sample is still open | **Keep in research**; do not put on runtime signal authority |
| Binance testnet adapter | Explicit sandbox/testnet restrictions and dedicated safety regressions; not part of H011 public order path | **Keep behind testnet-only guard**; not authorized for live trading |
| Synthetic demo scheduler | Disabled unless a dedicated explicit opt-in is set; distinct from current public real-data view | **Keep dormant and gated**, not safe to delete without broader legacy/app impact analysis |
| Private admin/control application | Not mounted in the public H011 entrypoint; separate privileged surface | **Keep unmounted**; never expose it through OpenSRE |
| OpenSRE observer | Public-alpha external SRE agent; may offer remediation depending on tool grants | **Add optional read-only bridge only**; no H011 dependency, secrets, or auto-remediation |

## Compatibility and safety

Legacy T0 packet schema/version `gptrader.t0.v1`, packet prefix `gpt0-`, cursor prefix `gptrader.v1:`, recovery marker and stored directory `/app/polymarket/results/gptrader` are persisted-data compatibility identifiers only. Renaming those formats in place would make old evidence harder to read; the model/service code is removed independently.

No destructive deletion of data volumes, local workspaces or historical audit evidence is part of this change. The retired provider's old outcome/evidence notes remain archived for audit, but they are not executable code or current runtime claims.

## Deployment acceptance

- CI passes migrated T0 lineage/sealer/replication tests and the full suite.
- Build contains no `backend/gptrader/`, provider profile or MCP listener.
- Public OpenAPI has no `/api/gptrader/*` routes and no mutating methods.
- H011 is deployed only after review; then `/healthz`, `/readyz`, provenance and PAPER locks are read back from the actual runtime.
- Expected state: `PAPER`, `EDGE=UNPROVEN`, `orders_enabled=false`, `live_capital_locked=true`, `hard_paper_lock=true`, `LIVE=false`.
