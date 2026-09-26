# Grokbot GPTrader workspace

Authority: https://github.com/simondalmasso/SeneX/issues/73  
Order: ORDER085  
Branch: `grokbot/order085-gptrader-design`  
Base main at workspace creation: `b5ca74f2cc0d0961e211388ca54884e896635be2`

This directory is Grokbot's durable design workspace for GPTrader.

Allowed writes in ORDER085:
- `research/gptrader/grokbot/**`
- comments/checkpoints on Issue #73

Forbidden:
- product code
- tests/workflows/canon docs
- ORDER084 artifacts
- H011/Cloudflare/D1/deployments
- model weights/thresholds
- LIVE/order-capability changes

This order is DESIGN-ONLY. The branch must not be merged merely because the design is complete.

Files:
- `HANDOFF.md`: binding instruction
- `DESIGN.md`: evolving implementation-ready architecture
- `CHECKPOINT.md`: resumable checkpoint, update often
- `RESEARCH_ANCHORS.md`: starting evidence and external constraints

If quota ends, update and commit `CHECKPOINT.md` before returning.
