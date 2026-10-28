#!/bin/sh
# SENECIO ORACLE ? SENEX-SCORE-002
# Passive public monitor only: no reconciliation process or database writer.
set -eu
unset SENEX_T0_REPLICATION_TOKEN
unset SENEX_GPTRADER_INGEST_TOKEN
exec uvicorn backend.main_real:app \
  --host 0.0.0.0 \
  --port 8080 \
  --workers 1 \
  --log-level info \
  --no-access-log
