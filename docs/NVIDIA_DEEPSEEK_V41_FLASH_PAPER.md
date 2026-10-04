# NVIDIA DeepSeek V4.1 Flash — isolated SENEX PAPER provider

Status: **prepared, not connected**.

This integration is deliberately outside the SENEX predictor and execution
paths. It reuses the existing GPTrader external decision contract, where an
external model can only return `TAKE` or `ABSTAIN` for an already-sealed T0
packet. SENEX owns direction. The external model cannot change direction,
position size, stop/target, venue, order type, or capital.

## Why this is low-impact

SENEX already has a vendor-neutral `OpenAICompatibleDecisionAdapter`. NVIDIA
NIM exposes DeepSeek V4.1 Flash through the same chat-completions wire shape.

No NVIDIA SDK is required and no provider-specific runtime code is added.

Provider profile:

```text
base_url = https://integrate.api.nvidia.com/v1
model    = deepseek-ai/deepseek-v4.1-flash
secret   = NVIDIA_API_KEY
```

The secret must remain in an authorized runtime secret store. Never commit it,
paste it into a ticket/chat, or write it to an env file in the repository.

## Stage 0 — provider-only probe

This stage never contacts SENEX MCP and never submits a decision.

From repository root:

```bash
export NVIDIA_API_KEY='<secret from runtime secret store>'
python -m research.gptrader.providers.nvidia_deepseek_v41_flash
```

Expected safe success shape:

```json
{
  "status": "PASS",
  "provider": "NVIDIA_NIM",
  "model": "deepseek-ai/deepseek-v4.1-flash",
  "paper_only": true,
  "simulation_only": true,
  "mcp_contacted": false,
  "decision_submitted": false
}
```

The returned action may be TAKE or ABSTAIN; this probe is a wire/schema test,
not a trading test.

If the key is absent, the probe exits before network with
`BLOCKED_NO_NVIDIA_API_KEY`.

## Stage 1 — one-shot GPTrader PAPER canary

Do not schedule this until Stage 0 passes.

Reuse the existing SENEX GPTrader MCP URL/token and map the NVIDIA provider into
the generic provider variables:

```bash
export SENEX_GPTRADER_MCP_URL='<existing PAPER MCP endpoint>'
export SENEX_GPTRADER_MCP_TOKEN='<existing PAPER MCP bearer>'

export SENEX_DECISION_PROVIDER_BASE_URL='https://integrate.api.nvidia.com/v1'
export SENEX_DECISION_PROVIDER_MODEL='deepseek-ai/deepseek-v4.1-flash'
export SENEX_DECISION_PROVIDER_API_KEY="$NVIDIA_API_KEY"

export SENEX_GPTRADER_BATCH_LIMIT='1'

python -m senecio_polymarket.backend.gptrader.external_client \
  --run-id 'NVIDIA_DS_V41_CANARY_001'
```

Required readback before any recurrence:

- `paper_only=true`;
- `simulation_only=true`;
- `live=false`;
- provider output normalizes to exactly packet_id/action/reason_codes;
- zero direction/flip fields;
- zero secret leakage;
- one bounded batch only;
- no real exchange/order/capital path.

## Stage 2 — comparative PAPER experiment

Only after a successful one-shot canary.

Recommended design:

- same sealed packet stream;
- frozen SENEX baseline;
- DeepSeek V4.1 Flash = treatment;
- GLM 5.3 or deterministic policy = comparator/control if desired;
- record latency, provider failures, JSON/schema failures, TAKE/ABSTAIN rate,
  and downstream PAPER result;
- no provider sees settlement/outcome information at T0;
- no provider can change direction.

Do not optimize prompts against the final holdout. Freeze prompt/version before
prospective evaluation.

## Explicit prohibitions

This integration does **not** authorize:

- enabling Binance or any exchange API;
- changing SENEX direction;
- changing size/leverage/stops/targets;
- submitting real orders;
- enabling LIVE/capital;
- treating model confidence as calibrated probability;
- using an LLM to rescue the ORDER099 null result post hoc.

Current scientific authority remains:

```text
INCREMENTAL_EDGE_NOT_DEMONSTRATED
EDGE=UNPROVEN
```

DeepSeek is therefore an experimental PAPER filter/agent, not evidence of edge.
