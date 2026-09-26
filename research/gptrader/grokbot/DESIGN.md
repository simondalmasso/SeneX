# GPTrader Design — ORDER085

STATUS: NOT_STARTED_BY_GROKBOT

Authority: https://github.com/simondalmasso/SeneX/issues/73

> Grokbot owns this document. Replace the placeholder sections with measured evidence and an implementation-ready design. Do not write product code in ORDER085.

## 1. Fresh measured state

## 2. Existing SENEX prediction and PAPER execution path

## 3. Constraints and non-goals

## 4. Architecture alternatives

### A. Internal SENEX agent

### B. External hourly ChatGPT agent

### C. Hybrid sealed-batch agent

## 5. Recommended architecture

## 6. End-to-end data flow

## 7. MCP tools and schemas

## 8. Authentication and threat model

## 9. Cursor, idempotency and recovery

## 10. Hourly scheduled-task protocol

## 11. No-lookahead and stale-signal semantics

## 12. PAPER execution integration

## 13. Persistence and Cloudflare/D1 budget

## 14. Scientific evaluation and calibration diagnostics

## 15. Dashboard contract

## 16. Failure modes and fail-closed behavior

## 17. Test/verification strategy

## 18. Implementation decomposition

## 19. Open owner decisions

## 20. Final self-review

Check specifically for:
- future/outcome leakage;
- duplicate execution engines;
- accidental LIVE capability;
- D1 amplification;
- non-idempotent retries;
- stale signal pretending to be live;
- conflating GPTrader PnL with SENEX EDGE;
- inability to conclude SENEX signal is not useful.
