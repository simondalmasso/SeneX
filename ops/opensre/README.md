# OpenSRE × SENEX — read-only operations bridge

Status: optional external observer; not part of the H011 container or trading path.

OpenSRE (Tracer-Cloud/opensre) is a public-alpha SRE agent. Treat it as an incident-analysis tool, not an execution or profitability component. This adapter collects a small H011 health snapshot and deliberately provides no write actions.

## Run

From the repository root, using Python's standard library only:

```powershell
python ops/opensre/h011_readonly_probe.py --json
```

To ask OpenSRE to review the emitted evidence, pipe the JSON into its headless CLI:

```powershell
python ops/opensre/h011_readonly_probe.py --json | opensre ask "Review this SENEX health snapshot. Cite only observed fields. Flag missing, stale or contradictory evidence. Do not perform remediation, deploys, account access, order calls or live-capital actions."
```

The probe only permits the allowlisted public H011 HTTPS hostname; it rejects arbitrary hosts, query-bearing base URLs and redirects. Requests are GET-only to `/healthz`, `/readyz`, `/api/runtime/provenance`, `/api/oracle/score?symbol=BTCUSDT`, `/api/portfolio/live_gate?symbol=BTCUSDT` and `/openapi.json`. It reports a degraded/incomplete status if the required safety state cannot be observed.

## Boundaries

- No account, balance, order, cancellation, credential or private-admin endpoints are queried.
- The adapter does not read `.env`, API keys, tokens or cookies.
- Redirects are rejected; no write-capable remediation tools are configured by this integration.
- OpenSRE is invoked separately by the operator. It is not installed as a runtime package, not deployed to Northflank, and cannot cause H011 mutations.
- Do not grant the external agent access to browser sessions, secrets, private routes or unrestricted shell remediation.
- `PAPER_ONLY=true`, `EDGE=UNPROVEN`, `LIVE=false`, real orders disabled and live capital locked remain independent controls.

Upstream: https://github.com/Tracer-Cloud/opensre. Verify its release, source and security posture before installing it on an operator machine; public-alpha APIs and integrations may change.
