# ORDER126 — NVIDIA DeepSeek V4.1 Flash isolated shadow provider

Purpose: evaluate `deepseek-ai/deepseek-v4.1-flash` without placing the model,
its API key, or any exchange capability inside H011.

## Provider profile

Use the NVIDIA hosted OpenAI-compatible NIM endpoint:

```text
SENEX_DECISION_PROVIDER_BASE_URL=https://integrate.api.nvidia.com/v1
SENEX_DECISION_PROVIDER_MODEL=deepseek-ai/deepseek-v4.1-flash
SENEX_DECISION_PROVIDER_MAX_TOKENS=1024
SENEX_GPTRADER_BATCH_LIMIT=4
```

Store `SENEX_DECISION_PROVIDER_API_KEY` only in the external worker secret
store. Never commit or paste it into GitHub, logs, prompts, or H011 runtime
configuration.

The worker also needs only the scoped GPTrader PAPER MCP/HTTP URL + bearer.
It must not receive Binance, Supabase, D1, Northflank, deployment, wallet, or
exchange-signing credentials.

## Why this is isolated

The existing SENEX provider adapter is vendor-neutral and OpenAI-compatible.
It sends only sealed T0 packets and accepts only:

```json
{
  "decisions": [
    {
      "packet_id": "...",
      "action": "TAKE|ABSTAIN",
      "reason_codes": ["..."]
    }
  ]
}
```

The validator rejects direction changes, flips, sizing, prices, orders and
unknown fields. SENEX deterministic code remains owner of direction, risk,
sizing and execution.

ORDER126 additionally adds `--shadow-only`: one sealed packet batch is fetched,
sent to the provider, normalized, and returned without calling
`submit_paper_decisions`.

## Stage 0 — offline compatibility

```bash
python -m pytest -q \
  tests/test_order092_external_client.py \
  tests/test_order092_provider_compat.py
```

This uses no NVIDIA credential and no network.

## Stage 1 — real NVIDIA shadow probe, no submit

In an external worker with secrets injected by the platform:

```bash
export SENEX_DECISION_PROVIDER_BASE_URL="https://integrate.api.nvidia.com/v1"
export SENEX_DECISION_PROVIDER_MODEL="deepseek-ai/deepseek-v4.1-flash"
export SENEX_DECISION_PROVIDER_MAX_TOKENS="1024"
export SENEX_GPTRADER_BATCH_LIMIT="4"

# secret-store injected; do not print:
# SENEX_DECISION_PROVIDER_API_KEY
# SENEX_GPTRADER_MCP_URL
# SENEX_GPTRADER_MCP_TOKEN

python -m senecio_polymarket.backend.gptrader.external_client \
  --run-id "NVIDIA_V41_SHADOW_001" \
  --shadow-only
```

Expected properties:

- provider call succeeds or fails closed;
- output is strict normalized JSON;
- `submitted=false`;
- GPTrader state/cursor is not advanced;
- no order, position, balance, LIVE or capital surface is touched.

Do not schedule repeated `--shadow-only` runs as a production loop: because
the server cursor is intentionally not advanced, repeated probes may evaluate
the same packet prefix. Stage 1 is a compatibility probe.

## Stage 2 — PAPER canary

Only after Stage 1 output validity is demonstrated, remove `--shadow-only`.
The existing client then submits normalized decisions to the GPTrader PAPER
surface. The MCP readiness gate must still report PAPER/simulation-only.

Recommended cadence: one-shot external jobs, small batches. Do not run a
persistent agent.

## Efficiency

V4.1 Flash has a very large context window, but SENEX should not use it here.
The decision task is deliberately bounded:

- batch size: 4 initially;
- output cap: 1,024 tokens;
- text-only sealed packets;
- no images;
- no repo dump;
- no full journal/history;
- no repeated retry storm;
- idempotent PAPER submissions only.

The goal is not to maximize context consumption. It is to measure whether this
provider adds reproducible prospective value over the existing sealed packet
decision problem.

## Scientific gate

ORDER099's completed result is
`INCREMENTAL_EDGE_NOT_DEMONSTRATED`.

DeepSeek V4.1 Flash is therefore a new prospective hypothesis, not a rescue of
the historical null. Its output may not be used to justify LIVE or real
capital without a fresh preregistered evaluation on untouched data.

## Self-hosting

At the time of this order, NVIDIA lists the V4.1 Flash free/partner hosted
endpoint as available but does not list V4.1 Flash as downloadable. Therefore
the isolation boundary is process/network/credential isolation around the
hosted endpoint, not a self-hosted model inside SENEX.
