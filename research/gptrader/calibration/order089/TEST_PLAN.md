# ORDER089 Test Plan

All tests are offline/read-only. They must not import or mutate ORDER086 runtime state.

## Recovery and quarantine

1. Packet at 2026-09-27T21:29:52.999Z is not incident-time quarantined solely by timestamp.
2. Packet at 2026-09-27T21:29:53Z is quarantined.
3. Packet at 2026-09-27T22:10:22.999Z is quarantined.
4. Packet at 2026-09-27T22:10:23Z is outside the incident epoch, but cannot become clean until restored-runtime provenance is valid.
5. IDs 6868/6870/6872 are quarantined regardless of timestamp.
6. Quarantine rows remain physically preserved.
7. Any derived settlement/science row referencing a quarantined prediction/packet is excluded.

## Chronological split

8. TRAIN and CALIBRATION are contiguous and non-overlapping.
9. HOLDOUT starts after artifact freeze and cannot overlap TRAIN/CALIBRATION.
10. Any random time shuffle is rejected.
11. Same-hour BTC/ETH map to one primary independent 1h cluster.
12. 14 days cannot claim 600 hourly clusters; maximum is 336.
13. Strong holdout gate refuses <600 clusters or <25 represented days.

## Calibration contract

14. JSON schema parses as draft 2020-12.
15. calibration_id matches canonical core SHA256.
16. Changing model_provider/model_name alone does not change calibration_id.
17. Changing threshold/search-space/source-epoch hash does change calibration_id.
18. Allowed actions are exactly TAKE/ABSTAIN.
19. A calibrated gate may veto TAKE to ABSTAIN but may never promote ABSTAIN to TAKE or alter direction.
20. Risk scaling is rejected in ORDER089 v1.
21. Candidate set contains at most eight preregistered candidates.

## No lookahead

22. All arms receive the identical sealed T0 packet hash.
23. Settlement accessor is unavailable until CONTROL, DECISION_AGENT, and CALIBRATED_AGENT decisions are frozen.
24. Current-market/network/outcome fields are absent from decision-time inputs.
25. HOLDOUT outcomes cannot update the fixed artifact.

## Agent/model neutrality

26. Mock LLM A, mock LLM B, and deterministic rule client use the same DecisionEnvelope schema.
27. Vendor/model metadata changes replay provenance only.
28. No vendor SDK is required by the calibration artifact/schema.
29. Replacing an agent does not change T0/H2/MCP/persistence/scheduler topology.
30. New agent starts shadow evidence and does not inherit old-model performance evidence.

## Rollback

31. Missing/corrupt artifact fails closed.
32. Artifact ID/hash mismatch fails closed.
33. Source epoch/quarantine hash mismatch fails closed.
34. Insufficient TRAIN/CALIBRATION data blocks fixed-calibration PAPER.
35. Failed holdout restores last known fixed calibration_id without refit.
36. Rollback preserves failed candidate evidence and starts a new evaluation epoch.

## Acceptance commands

Static research-only checks may:
- parse CALIBRATION_ARTIFACT_SCHEMA.json;
- assert banned vendor names do not appear in schema semantics;
- assert incident IDs and epoch constants appear consistently across documents;
- assert stage thresholds 168/7, 168/7, and 600/25 are consistent.

No test may contact D1, an exchange, a broker, a wallet, a model vendor, current-market APIs, H011, the Decision MCP runtime, or the hourly scheduler.
