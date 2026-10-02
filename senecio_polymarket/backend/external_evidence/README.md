# ORDER095 external evidence shadow fabric

This package captures **public external evidence only**. It is intentionally not imported by SENEX prediction generation or GPTrader decision code.

## Scientific boundary

Every persisted row is hard-coded:

```text
shadow_only=true
decision_allowed=false
t0_allowed=false
```

ORDER095 therefore creates a prospective evidence corpus; it does not add features, thresholds, signals, trading actions, or execution authority.

Promotion into any T0 experiment requires a separate preregistered hypothesis and AUD gate.

## Storage

Default root:

`$SENEX_RESULTS_DIR/external_evidence`

or `/app/polymarket/results/external_evidence` when the persistent results volume exists, otherwise `data/external_evidence`.

- `events.jsonl`: append-only hash-chained metadata + normalized bounded text
- `blobs/<sha256>`: immutable content-addressed raw capture

No D1 is used.

## Providers

### Scrapling

Optional, out-of-process, no SENEX dependency pin:

```bash
SENEX_SCRAPLING_BIN=/path/to/scrapling \
python -m senecio_polymarket.backend.external_evidence.cli collect \
  --provider scrapling \
  --url https://example.com/public-page
```

The adapter uses the documented `scrapling extract get` CLI with `--ai-targeted`. It passes no cookies, authorization headers, proxy credentials, or browser profile.

### Agent-Reach

Agent-Reach 1.5.0 is an installer/skill surface that routes to channel-specific tools rather than exposing one stable read command. SENEX therefore does **not** pretend `agent-reach` itself is a universal scraper.

Configure an owner-controlled bridge executable:

```text
SENEX_AGENT_REACH_BRIDGE=/opt/senex/agent-reach-bridge
```

SENEX writes one JSON request to the bridge's stdin and expects one bounded JSON response on stdout. Browser/channel credentials remain entirely outside SENEX.

### Patchright Enhanced / GhostProbe

Patchright Enhanced is a Node/browser library, not a stable SENEX CLI. Configure an isolated bridge:

```text
SENEX_PATCHRIGHT_BRIDGE=/opt/senex/patchright-bridge
```

The bridge contract is identical to Agent-Reach. It must emit public normalized capture data only. Cookies, browser storage, request authorization and private session state must never be returned.

## Bridge request

```json
{
  "contract": "senex.external_evidence.bridge_request.v1",
  "url": "https://public.example/resource",
  "shadow_only": true,
  "decision_allowed": false,
  "t0_allowed": false
}
```

## Bridge response

```json
{
  "source_kind": "social",
  "native_id": "platform-id",
  "published_at": "2026-10-02T10:00:00Z",
  "observed_at": "2026-10-02T10:00:01Z",
  "content": "normalized public content",
  "raw": "raw public content",
  "provider_version": "pinned-version",
  "metadata": {
    "platform": "x"
  }
}
```

Sensitive metadata keys such as tokens, cookies, authorization, passwords, API keys and private keys are rejected.

## Target guard

Only `http://` and `https://` URLs are accepted. The core rejects:

- URL-embedded credentials
- localhost/local/internal names
- literal loopback/private/link-local/reserved/multicast addresses
- non-HTTP schemes

Provider bridges remain responsible for enforcing the same rule across redirects and DNS resolution.

## Verify

```bash
python -m senecio_polymarket.backend.external_evidence.cli verify
```

The verifier checks the JSONL hash chain, duplicate IDs, raw blob existence and raw blob SHA-256.

## Not authorized in ORDER095

- predictor imports
- GPTrader imports or task changes
- live features
- current-price enrichment
- trading decisions
- D1
- deploy/restart
- LIVE/orders/capital
- paid infrastructure/API fallback
