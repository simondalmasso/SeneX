#!/bin/sh
# SENECIO ORACLE — SENEX-SCORE-002
# Production settlement authority remains backend.oracle_runner.
# settlement_reconciler is repair-only and never settles NULL rows.
set -u

HEARTBEAT_FILE="${SENEX_RECONCILER_HEARTBEAT_FILE:-/tmp/senex-reconciler-heartbeat}"
HEALTH_GRACE_S="${SENEX_RECONCILER_HEALTH_GRACE_SEC:-120}"
HEALTH_STALE_S="${SENEX_RECONCILER_HEALTH_STALE_SEC:-1200}"
MCP_ENABLED="${SENEX_GPTRADER_MCP_ENABLED:-0}"
MCP_PORT="${SENEX_GPTRADER_MCP_PORT:-8787}"
MCP_RESULTS_DIR="${SENEX_GPTRADER_MCP_RESULTS_DIR:-/app/polymarket/results/gptrader-mcp-runtime}"
MCP_PID=""
MCP_TOKEN_VALUE="${SENEX_GPTRADER_MCP_TOKEN:-}"
INGEST_TOKEN_VALUE="${SENEX_GPTRADER_INGEST_TOKEN:-}"
unset SENEX_GPTRADER_MCP_TOKEN
unset SENEX_GPTRADER_INGEST_TOKEN

# Portable integer validation: invalid operator overrides fail closed.
case "$HEALTH_GRACE_S" in ''|*[!0-9]*) echo "[start_single_authority.sh] FATAL: invalid SENEX_RECONCILER_HEALTH_GRACE_SEC" >&2; exit 78;; esac
case "$HEALTH_STALE_S" in ''|*[!0-9]*) echo "[start_single_authority.sh] FATAL: invalid SENEX_RECONCILER_HEALTH_STALE_SEC" >&2; exit 78;; esac
case "$MCP_ENABLED" in 0|1) ;; *) echo "[start_single_authority.sh] FATAL: SENEX_GPTRADER_MCP_ENABLED must be 0 or 1" >&2; exit 78;; esac
case "$MCP_PORT" in ''|*[!0-9]*) echo "[start_single_authority.sh] FATAL: invalid SENEX_GPTRADER_MCP_PORT" >&2; exit 78;; esac

if [ -z "${SUPABASE_URL:-}" ] || [ -z "${SUPABASE_KEY:-}" ]; then
  echo "[start_single_authority.sh] FATAL: SUPABASE_URL and SUPABASE_KEY are required" >&2
  exit 78
fi

rm -f "$HEARTBEAT_FILE"

echo "[start_single_authority.sh] launching settlement authority + reconciliation guard..."

start_reconciler() {
  echo "[start_single_authority.sh] launching reconciliation guard..."
  python -m backend.settlement_reconciler &
  RECONCILER_PID=$!
  RECONCILER_STARTED_AT=$(date +%s)
}

start_reconciler

if [ "$MCP_ENABLED" = "1" ]; then
  if [ ! -d /app/polymarket/results ]; then
    echo "[start_single_authority.sh] FATAL: persistent H011 volume is not mounted" >&2
    exit 78
  fi
  case "$MCP_RESULTS_DIR" in
    /app/polymarket/results/*) ;;
    *) echo "[start_single_authority.sh] FATAL: MCP results dir must live under persistent H011 volume" >&2; exit 78;;
  esac
  mkdir -p "$MCP_RESULTS_DIR"
  export SENEX_GPTRADER_VIEW_ROOT="$MCP_RESULTS_DIR/gptrader"
  echo "[start_single_authority.sh] launching GPTrader Decision MCP sidecar on port ${MCP_PORT}..."
  SENEX_GPTRADER_MCP_TOKEN="$MCP_TOKEN_VALUE" \
  SENEX_GPTRADER_INGEST_TOKEN="$INGEST_TOKEN_VALUE" \
  SENEX_RESULTS_DIR="$MCP_RESULTS_DIR" \
    uvicorn backend.gptrader.mcp_http:create_app_from_env \
      --factory \
      --host 0.0.0.0 \
      --port "$MCP_PORT" \
      --workers 1 \
      --log-level info \
      --no-access-log &
  MCP_PID=$!
fi

# Production entrypoint intentionally uses main_real: synthetic market scheduler.
# It receives only the H2 ingest credential, never the Decision MCP bearer.
# Synthetic market scheduler is disabled unless explicitly supplied.
SENEX_GPTRADER_INGEST_TOKEN="$INGEST_TOKEN_VALUE" \
uvicorn backend.main_real:app \
  --host 0.0.0.0 \
  --port 8080 \
  --workers 1 \
  --log-level info \
  --no-access-log &
UVICORN_PID=$!

cleanup() {
  echo "[start_single_authority.sh] cleanup: stopping reconciler (${RECONCILER_PID:-none}), public uvicorn (${UVICORN_PID:-none}), MCP (${MCP_PID:-none})"
  kill -TERM "${RECONCILER_PID:-}" 2>/dev/null || true
  kill -TERM "${UVICORN_PID:-}" 2>/dev/null || true
  kill -TERM "${MCP_PID:-}" 2>/dev/null || true
  wait "${RECONCILER_PID:-}" 2>/dev/null || true
  wait "${UVICORN_PID:-}" 2>/dev/null || true
  wait "${MCP_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while true; do
  if ! kill -0 "$UVICORN_PID" 2>/dev/null; then
    echo "[start_single_authority.sh] uvicorn exited — shutting down container" >&2
    break
  fi
  if [ "$MCP_ENABLED" = "1" ] && ! kill -0 "$MCP_PID" 2>/dev/null; then
    echo "[start_single_authority.sh] MCP sidecar exited — shutting down container" >&2
    break
  fi

  NOW=$(date +%s)
  if ! kill -0 "$RECONCILER_PID" 2>/dev/null; then
    echo "[start_single_authority.sh] reconciler exited — restarting repair guard" >&2
    start_reconciler
  fi

  # PID liveness alone is insufficient: require a recent completed reconcile cycle.
  if [ -f "$HEARTBEAT_FILE" ]; then
    HEARTBEAT_MTIME=$(python -c 'import os,sys; print(int(os.path.getmtime(sys.argv[1])))' "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
    AGE=$((NOW - HEARTBEAT_MTIME))
    if [ "$AGE" -gt "$HEALTH_STALE_S" ]; then
      echo "[start_single_authority.sh] FATAL: reconciler heartbeat stale age=${AGE}s limit=${HEALTH_STALE_S}s" >&2
      exit 1
    fi
  else
    START_AGE=$((NOW - RECONCILER_STARTED_AT))
    if [ "$START_AGE" -gt "$HEALTH_GRACE_S" ]; then
      echo "[start_single_authority.sh] FATAL: reconciler produced no heartbeat within ${HEALTH_GRACE_S}s" >&2
      exit 1
    fi
  fi

  sleep 2
done
exit 1
