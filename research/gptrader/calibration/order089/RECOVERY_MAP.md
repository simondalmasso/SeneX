# ORDER089 Recovery Map

Status: offline research only. No ORDER086 runtime file, H011 deployment, MCP activation, scheduler, D1, LIVE, broker, wallet, signer, or real-order surface is modified.

## Recovery ruling

SENEX can recover without restarting the entire historical experiment. Preserve pre-incident canonical evidence, quarantine the incident exactly, and start a new post-restoration calibration baseline. The incident is a discontinuity in calibration eligibility, not permission to erase history.

The clean baseline boundary is deterministic:

`clean_baseline_start = first sealed T0 packet with packet/payload timestamp >= 2026-09-27T22:10:23Z, outside prediction IDs {6868,6870,6872}, produced by the restored canonical runtime, and passing packet hash/schema/no-future-field checks.`

If runtime provenance cannot prove the restored canonical source for that first packet, the baseline remains UNKNOWN and calibration cannot start. Do not guess a timestamp or packet sequence.

## Epoch table

| Epoch | Deterministic boundary | Allowed use | Forbidden use |
|---|---|---|---|
| E0 PRE_INCIDENT_CANONICAL | timestamp < 2026-09-27T21:29:53Z and previously canonical | SCIENCE, historical reference, sensitivity checks | selecting a post-incident calibration candidate |
| E1 INCIDENT_QUARANTINED | [2026-09-27T21:29:53Z, 2026-09-27T22:10:23Z), prediction IDs 6868/6870/6872, plus any sealed T0 packet inside the epoch | FORENSIC_ONLY | TRAIN, CALIBRATE, HOLDOUT, canonical SCIENCE |
| E2 CLEAN_TRAIN | first 168 independent 1h clusters from clean_baseline_start, spanning at least 7 calendar days | TRAIN only: derive T0-only quantile cut points and enumerate preregistered candidates | candidate scoring on the same rows |
| E3 CLEAN_CALIBRATION | next 168 independent 1h clusters, spanning at least 7 additional calendar days | CALIBRATE: select one frozen candidate from the preregistered set | refitting thresholds after viewing holdout |
| E4 FIXED_PAPER_HOLDOUT | starts only after calibration artifact freeze; no overlap with E2/E3 | SHADOW_PAPER, FIXED_CALIBRATION_PAPER, HOLDOUT, SCIENCE after decisions are frozen | tuning/reselection |
| E5 REPLACEMENT_AGENT_SHADOW | any later agent/model replacement under the same Decision MCP protocol | compatibility SHADOW_PAPER and new provenance evidence | inheriting prior model-specific performance claims |

Same-hour BTC/ETH observations share one independent 1h cluster. Chronological ordering is mandatory. No random shuffle across time.

## Incident quarantine

- Preserve prediction IDs 6868, 6870, 6872.
- Preserve every incident-epoch T0 packet for forensics.
- Exclude every quarantined row and derived settlement/science contribution from canonical evaluation.
- Never relabel quarantined data as clean after later recovery.
- Quarantine membership is determined before any calibration search.

## Continuity rule

E0 may remain part of the historical SENEX science record. E2 begins a new calibration epoch because the runtime incident breaks calibration continuity. A result may report E0 and E4 side by side, but a post-incident candidate must be selected only from E2/E3 and judged on E4.

## Agent replacement rule

Agent/model identity is provenance metadata, not an epoch-selection input and not part of calibration semantics. Replacing an LLM does not change H011, T0 sealing, H2, the Decision MCP schema, persistence topology, or the calibration artifact. The replacement agent starts a fresh shadow-evidence stream before its results are compared scientifically.
