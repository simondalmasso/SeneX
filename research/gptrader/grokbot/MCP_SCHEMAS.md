# GPTrader MCP schemas — ORDER085

Decision MCP is a narrow PAPER-only tool surface.

## get_gptrader_health

Input: none.

Output includes:
```json
{
  "ok": true,
  "paper_only": true,
  "live": false,
  "schema_version": "gptrader.v1",
  "source_commit": "...",
  "volume_ready": true,
  "cursor_ready": true
}
```

No outcomes or current market data.

## get_prediction_batch

Input:
```json
{"cursor":"opaque-or-null","limit":8}
```

Rules:
- 1 <= limit <= 16
- keyset cursor over monotonic packet_seq
- sealed decision-time packets only
- never outcomes/results/current prices
- bounded packet projection

Output:
```json
{
  "batch_id":"...",
  "cursor_in":"...",
  "next_cursor":"...",
  "has_more":false,
  "packets":[]
}
```

## get_gptrader_state

Decision-safe hypothetical portfolio state only. Must not leak recently settled packet outcomes, current market data, or Review MCP content.

## submit_paper_decisions

Input:
```json
{
  "run_id":"...",
  "cursor":"...",
  "decisions":[
    {
      "packet_id":"...",
      "action":"TAKE",
      "reason_codes":["..."],
      "idempotency_key":"..."
    }
  ]
}
```

Primary action enum = TAKE | ABSTAIN.

Primary risk sizing is fixed and preregistered. Optional `size_scale` may be recorded only as secondary exploratory metadata.

Forbidden:
- direction override / FLIP
- LIVE
- broker/wallet/signer
- arbitrary notional
- model tuning
- D1/database operation
- arbitrary URL/backend/shell invocation

Atomic/idempotent semantics:
- stable unique (policy_id, packet_id)
- first committed decision wins
- identical retry => duplicate, no new fill
- conflicting retry => CONFLICT_ALREADY_DECIDED
- cursor mismatch => zero apply
- cursor advances only after durable decision commit
- replay/settlement cannot run before commit

## Review MCP

Separate connector/token, never installed in scheduled decision task. May expose settled results and verdicts to reviewers only.
