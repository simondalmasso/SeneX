# GPTrader Research Anchors

These are starting anchors, not substitutes for fresh verification.

## Repository evidence at workspace creation

Base main: `b5ca74f2cc0d0961e211388ca54884e896635be2`

Relevant current paths:
- `senecio_polymarket/backend/oracle_runner.py`
- `senecio_polymarket/backend/portfolio/coordinator.py`
- `senecio_polymarket/backend/portfolio/execution_engine.py`
- `senecio_polymarket/backend/portfolio/trade_journal.py`
- `senecio_polymarket/backend/paper_view.py`
- `senecio_polymarket/backend/main_real.py`
- `senecio_polymarket/backend/oracle_engine.py`

Measured at seed time:
- prediction cadence target: 15 minutes;
- symbols: ETH/USDT and BTC/USDT;
- nominal opportunities: 8/hour, 192/day;
- hourly scheduler: 24 runs/day;
- batch polling can theoretically reduce polling calls by 87.5% versus one poll per prediction while retaining all events if cursoring is correct.

## Current external constraints to re-verify

OpenAI scheduled tasks documentation:
- https://help.openai.com/en/articles/10291617-scheduled-tasks-in-chatgpt
- current docs state eligible plans can run recurring tasks up to once per hour.

Cloudflare:
- https://developers.cloudflare.com/workers/
- https://developers.cloudflare.com/changelog/product/d1
- https://developers.cloudflare.com/workers/cache/configuration

Design goal is not to exploit quotas. It is to avoid redundant remote reads by keeping GPTrader's normal loop local/bounded and cursor-based.

## Evidence rule

Any time-sensitive external claim in DESIGN.md must record source URL + retrieval date. Repository claims should identify exact file/symbol/commit.
