# ORDER089 Promotion Ladder

Every transition is explicit. No stage transition mutates SENEX direction, H011, T0 sealing, H2, MCP transport, persistence topology, or scheduler topology.

## STAGE_0 — OFFLINE_REPLAY

Entry:
- clean baseline rule resolves;
- incident quarantine is loaded and hashed;
- TRAIN/CALIBRATION boundaries are frozen;
- search-space version is preregistered.

Actions:
- replay the same sealed T0 packets through CONTROL, DECISION_AGENT, and candidate gates;
- freeze arm decisions before settlement;
- select at most one candidate after CALIBRATION.

Exit to STAGE_1:
- artifact schema valid;
- calibration_id recomputes exactly;
- no-lookahead checks pass;
- no quarantined packet contributes;
- TRAIN >=168 independent 1h clusters over >=7 days;
- CALIBRATION >=168 additional independent 1h clusters over >=7 days.

## STAGE_1 — SHADOW_PAPER

The candidate observes live sealed T0 through the same Decision protocol but cannot affect the existing PAPER treatment. This stage is compatible with an LLM/model replacement and is the default first stage for a new agent identity.

Exit to STAGE_2:
- exact calibration artifact is fixed;
- adapter/client passes DecisionEnvelope conformance;
- no runtime/persistence/provenance mismatch;
- no new decision-time data source;
- candidate is not a trivial abstention policy under the preregistered coverage guard.

No profitability claim is allowed here.

## STAGE_2 — FIXED_CALIBRATION_PAPER

Run the fixed artifact in PAPER only. No refit, threshold drift, prompt search against outcomes, or adaptive parameter update is allowed.

The start of STAGE_2 is also the start of the independent HOLDOUT. Every candidate revision gets a new calibration_id and a fresh holdout.

Exit to STAGE_3:
- >=600 independent 1h holdout clusters;
- >=25 calendar days represented;
- quarantine overlap = 0;
- provenance complete;
- no candidate changes during the holdout.

## STAGE_3 — INDEPENDENT_HOLDOUT_VERDICT

Primary comparison:
- calibrated agent vs uncalibrated Decision-Agent;
- calibrated agent vs fixed SENEX control.

Use dependence-aware hourly-cluster evidence. Positive simulated PnL alone cannot pass the gate.

Outcomes:
- HOLDOUT_PASSED: evidence meets the preregistered rule. This is evidence for PAPER experimentation only; LIVE remains outside ORDER089.
- HOLDOUT_FAILED: keep artifact/evidence, block promotion, and roll back to the last fixed PAPER calibration.
- INSUFFICIENT_DATA: continue fixed holdout without retuning.

## Model/agent replacement

Changing model/vendor is an edge-client change, not a SENEX protocol migration:

1. revoke old credential;
2. configure new client/adapter identity;
3. retain Decision MCP, sealed T0 format, H2, persistence, history, scheduler topology, and calibration artifact;
4. enter STAGE_1 shadow with new provenance;
5. do not inherit the old model's scientific performance claim.

A non-LLM deterministic client follows the same sequence.
