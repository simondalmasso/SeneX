# NVIDIA DeepSeek V4.1 Flash — isolated SENEX PAPER provider profile

Status: **PREPARED / OFFLINE-ONLY / ZERO-COST OWNER LOCK**

This order converges the useful parts of PRs #129 and #131 without activating an
external provider.

## Owner policy

No operation in this order authorizes spending money or consuming a paid
provider quota. No external-provider network probe is enabled.

Even when `NVIDIA_API_KEY` is present, the provider-profile CLI returns:

```text
BLOCKED_EXTERNAL_PROVIDER_NETWORK_DISABLED
zero_cost_owner_lock=true
network_used=false
```

A future real NVIDIA request requires a separate reviewed code change and owner
authorization. A secret alone is not authorization.

## Reusable generic capability

The existing vendor-neutral GPTrader external client gains two bounded features:

- optional `SENEX_DECISION_PROVIDER_MAX_TOKENS`;
- `--shadow-only`, which evaluates one already-sealed packet batch but does
  not call `submit_paper_decisions` and does not advance the GPTrader cursor.

Neither feature activates itself. No provider URL, key, scheduler, or H011
configuration is changed by this order.

## Provider profile

The dormant NVIDIA-compatible profile records:

```text
base_url = https://integrate.api.nvidia.com/v1
model    = deepseek-ai/deepseek-v4.1-flash
secret   = NVIDIA_API_KEY
```

The provider-specific module is intentionally offline-only. Its `run_probe`
function requires an injected `http_post` transport, which is used by unit
tests only. Calling it without an injected transport fails before network.

## Isolation

This order does not grant the provider access to:

- H011 runtime mutation;
- GPTrader submit during the provider-specific probe;
- Binance or any exchange API;
- wallet, signer, order, balance, withdrawal, or capital capability;
- Supabase/D1/Northflank credentials;
- settlement/outcome evidence;
- proprietary full T0 audit payloads.

The synthetic fixture exercises only the strict existing SENEX
`packet_id/action/reason_codes` response contract. Direction, sizing, risk,
stops, targets, venue and execution remain SENEX-owned.

## Verification

Offline tests cover:

1. exact NVIDIA profile values;
2. secret not present in repository source;
3. OpenAI-compatible request shape through a fake injected transport;
4. rejection of forbidden model fields such as direction;
5. real-network refusal when no fake transport is injected;
6. CLI refusal both with and without an API key;
7. generic `--shadow-only` no-submit behavior;
8. optional bounded provider output cap.

No real NVIDIA request is part of CI.

## Scientific posture

Current authority remains:

```text
INCREMENTAL_EDGE_NOT_DEMONSTRATED
EDGE=UNPROVEN
```

This integration is infrastructure research only. It is not evidence of edge,
does not activate GPTrader recurrence, and does not authorize LIVE, orders, or
capital.
