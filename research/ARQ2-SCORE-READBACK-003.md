# ARQ2-SCORE-READBACK-003 — bounded COLD readback

Status: **BLOCK_REAL**

## Authority
- ARQ2 base: `d3a5cfe9fa32c44a56c593ae794f35a620980fa3`
- Reference CORE: `c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a`
- HYP-008 remains paused.
- EDGE remains UNPROVEN.

## Blocking evidence
The required existing gateway/COLD path is authenticated. The deployed Worker reports a secret named `GATEWAY_TOKEN`, but Wrangler exposes only the secret name, not its value. The local authorized desktop session has no `SUPABASE_URL`, `SUPABASE_KEY`, or `GATEWAY_TOKEN` environment values. GitLab CI intentionally provides only the unreachable/fake sandbox Supabase pair. The current H011 public app exposes the bounded authority projection and does not mount a full-audit readback proxy.

No credential value was printed, copied, persisted, or requested from Cloudflare.

## Safety / read-shape compliance
Because the authorized client credential was unavailable, **no COLD request was issued**. In particular:
- no direct D1 SQL fallback;
- no COUNT(*);
- no OFFSET scan;
- no `/counts`;
- no change to `AUTHORITY_HISTORY_SELECT`;
- no AuthoritySnapshot/canonical seal mutation;
- no CORE/runtime mutation;
- no deploy;
- no HYP-008 mutation;
- no new ledger.

## Phase 1
Not executed. Rows requested/received: **0/0**. Completeness is therefore **not measured**, and this result must not be classified as `EXISTING_AUDIT_INSUFFICIENT`; the data hypothesis was not falsified. The blocker is access to the already-authorized gateway path.

## Phase 2
Not executed. AUC, Q5-Q1, non-overlap cohort, and waterfall attrition remain unmeasured.

## Minimal unblock
Attach an authorized gateway client credential to the execution environment without exposing it to logs/chat, then rerun exactly one initial page of at most 80 settled BTCUSDT rows with explicit `audit`, numeric-id keyset ordering, and the frozen field list. Only if the ≥95% completeness gate passes may the readback extend to at most 600 rows.
