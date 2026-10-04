# ORDER098 — Polymarket BTC 5m Resolution Corpus

Research-only support for ORDER097.

## Purpose

Produce a reproducible, fail-closed JSONL corpus of resolved Polymarket
`btc-updown-5m-<start_ts>` markets using public Gamma evidence only.

This order does not modify the predictor, runtime, portfolio, GPTrader,
deployment, exchange credentials, orders, LIVE, or capital.

## Evidence contract

A record is accepted only when all of the following hold:

1. slug is exactly `btc-updown-5m-<integral 300s boundary>`;
2. event slug matches the requested slug;
3. market conditionId matches the T0 prediction identity when supplied;
4. event and market are closed;
5. `umaResolutionStatus == resolved`;
6. outcomes are exactly UP/DOWN;
7. terminal `outcomePrices` contain exactly one 1 and one 0;
8. if `eventMetadata.finalPrice` and `priceToBeat` are present, their
   implied winner agrees with the terminal outcome prices;
9. every supplied resolution timestamp is after the exact market end;
10. a non-empty Polymarket/Chainlink resolution source is present.

The SENEX row-level `outcome` field is never read as the 5m label.

Every accepted row includes a SHA256 hash of the canonical raw Gamma event.

## Output schema

The JSONL is directly compatible with ORDER097 `join_resolutions`:

```json
{
  "slug": "btc-updown-5m-1791069900",
  "condition_id": "0x...",
  "start_ts": 1791069900,
  "end_ts": 1791070200,
  "outcome": "DOWN",
  "resolved_at": 1791070295.0,
  "source": "POLYMARKET_GAMMA_RESOLVED_V1"
}
```

Additional provenance fields are retained and ignored safely by ORDER097.

## Preferred current source: ORDER072 D1 COLD mirror

The current Cloudflare ORDER072 gateway is alive at:

```text
https://senex-order072-d1-gateway.simondalmasso44.workers.dev
```

Its public `/health` reports `storage=d1` / `adapter=order074-postgrest`.
Full-audit reads are authenticated: an unauthenticated
`/rest/v1/oracle_predictions?...select=...,audit` request returns HTTP 401.

The gateway implementation rehydrates `audit` from the COLD
`oracle_prediction_audit_cold` table whenever the query requests the full
audit. Therefore **Supabase does not need to be restored merely for ORDER098**
if the export is executed in an already-authorized SENEX environment whose
`SUPABASE_URL` / `SUPABASE_KEY` point to this gateway.

Do not copy or expose the gateway credential. Run the GET-only exporter inside
the already-authorized environment, then move only the secret-free JSONL and
manifest to the research machine / Intern Discovery.

## Full T0 audit export

The public H011 `/api/oracle/predictions/db` endpoint is intentionally bounded
and strips the full decision-time audit. It is **not** an admissible source for
ORDER097.

Export the persisted full audit through the read-only database REST surface
before moving data into Intern Discovery.

The exporter performs GET only, paginates by the integer prediction id, and
writes a minimal causal projection containing only:

- `id`, `ts`, `symbol`;
- `pipeline.step2_features.up_prob`;
- `pipeline.step2_features.polymarket_context_v1`;
- `external_markets_v1.polymarket`;
- a SHA256 over the persisted source audit evidence.

It deliberately omits SENEX settlement outcomes and other post-T0 material.
The persisted settlement reconciler preserves existing audit keys and only
adds/repairs settlement evidence, so the exported decision-time subtrees are
not reconstructed from post-close state.

Prefer a read-only key if one exists:

```bash
export SUPABASE_URL='https://<authorized-origin>'
export SUPABASE_READ_KEY='<read-only-key>'
# SUPABASE_KEY is accepted only as a fallback when that is the existing
# authorized credential. Never print or persist the credential.

export OUT=/tmp/order098-export-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$OUT"

python research/edge/order098/export_t0_audit.py \
  --output "$OUT/t0_predictions.jsonl" \
  --manifest "$OUT/t0_export_manifest.json"
```

Verify the files before transfer:

```bash
sha256sum "$OUT/t0_predictions.jsonl" "$OUT/t0_export_manifest.json"
python - <<'PY'
import json, os, pathlib
p = pathlib.Path(os.environ["OUT"]) / "t0_export_manifest.json"
m = json.loads(p.read_text())
print({k: m[k] for k in (
    "contract", "fetched_rows", "projected_rows", "skipped_rows",
    "first_id", "last_id", "source_rows_sha256", "output_file_sha256"
)})
PY
```

Do **not** place database credentials on Intern Discovery merely to run this
experiment. Export in an already-authorized SENEX environment, verify the
manifest/hash, then upload only the secret-free JSONL + manifest to
`/data/datasets`.

## Local / Intern Discovery run

Use CPU. A GPU is unnecessary.

Recommended persistent layout:

```text
/data/SeneX
/data/datasets
/data/results/order098-<timestamp>
```

Clone the exact research branch:

```bash
cd /data
git clone https://github.com/simondalmasso/SeneX.git
cd SeneX
git checkout order098/polymarket-5m-resolution-corpus
python -m pip install -r senecio_polymarket/requirements.lock
```

Upload the verified T0 export first:

```text
/data/datasets/t0_predictions.jsonl
/data/datasets/t0_export_manifest.json
```

Then collect exact market identities and public resolutions:

```bash
OUT=/data/results/order098-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$OUT"

python research/edge/order098/polymarket_5m_resolutions.py \
  --predictions /data/datasets/t0_predictions.jsonl \
  --output "$OUT/resolutions.jsonl" \
  --manifest "$OUT/resolution_manifest.json"
```

The command exits non-zero when any requested market is rejected. Partial
output and a rejection manifest are still written for diagnosis, but partial
coverage is not silently promoted to a valid corpus.

## Three-market public schema smoke

The following historical slugs were manually inspected during ORDER098 design:

```text
btc-updown-5m-1791069300 -> UP
btc-updown-5m-1791069600 -> UP
btc-updown-5m-1791069900 -> DOWN
```

For all three, public Gamma evidence exposed:

- exact conditionId;
- closed=true;
- resolved status;
- terminal 1/0 outcome prices;
- post-close timestamps;
- Chainlink BTC/USD TWAP resolution source;
- finalPrice/priceToBeat consistent with the terminal winner.

These are schema/evidence examples only, not a scientific sample.

## Feeding ORDER097 without branch contamination

Do not merge research branches merely to run the experiment.

1. Run ORDER098 and persist `resolutions.jsonl` under `/data`.
2. Record the ORDER098 commit and manifest hashes.
3. Checkout the exact ORDER097 branch separately.
4. Run its offline harness against the same immutable predictions file and
   the persisted ORDER098 resolutions file.
5. Record ORDER097 commit, outputs, train/holdout counts and blockers.

Example after checking out ORDER097:

```bash
python research/edge/order097/market_prior_calibration.py \
  --predictions /data/datasets/t0_predictions.jsonl \
  --resolutions "$OUT/resolutions.jsonl"
```

## Promotion rule

Successful collection proves only that target-aligned labels can be built.

It does **not** prove incremental SENEX edge.

ORDER097 must still beat the equivalently trained market-only baseline on
causal holdout rows with adequate coverage and uncertainty analysis.
