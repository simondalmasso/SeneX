# GPTrader PAPER-only threat model — ORDER085

## Assets

- sealed T0 packets
- GPTrader decision/journal state
- MCP credentials
- public H011 read-only contract
- D1 quota
- frozen 011 integrity

## Trust boundaries

1. public H011 GET surface
2. Decision MCP
3. Review MCP
4. local durable GPTrader volume
5. D1 outside GPTrader trust
6. scheduled ChatGPT task

## Main abuses and controls

| Abuse | Control |
|---|---|
| public mutating /mcp on H011 | forbidden topology |
| D1 quota amplification | GPTrader loop has no D1 binding/import |
| ChatGPT sees future/current market | Decision MCP only; no web/crypto plugins/dashboard/review tools |
| outcome-bearing packet | denylist + refuse already-outcome-bearing source row |
| direction FLIP | schema allows TAKE/ABSTAIN only |
| LIVE field | hard PAPER lock + schema rejection + no broker |
| duplicate submit | idempotency key + policy/packet uniqueness |
| cursor rewind/skip | monotonic keyset cursor + CAS |
| settlement before decision | durable decision commit is a hard precondition |
| prompt injection | numeric/structured bounded packet schema |
| ORDER084 collision | ORDER085/086 isolated branch/scope |
| 011 early look | no 011 reads |

Residual accepted risks:
- ChatGPT nondeterminism;
- scheduled task pause;
- optional overlay unavailable;
- local volume loss has the same durability class as other runtime journals unless a later order adds backup.
