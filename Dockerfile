# SENEX B8.1 — provenance-first production topology
# Internal identity is baked from copied runtime bytes + provider commit +
# a Git-compatible tree SHA of the build context. OCI digest is NEVER
# written as ENV and MUST NOT participate in internal exact=true.
#
# Northflank injects NF_GIT_SHA at build time as the commit being built:
# https://northflank.com/docs/v1/application/secure/inject-secrets
# That ARG is consumed here and discarded; it is not persisted as ENV.

FROM python:3.11-slim AS source
WORKDIR /source
COPY . /source
RUN PYTHONPATH=/source python -c "from pathlib import Path; from senecio_polymarket.backend.artifact_identity import git_tree_sha; print(git_tree_sha(Path('/source')))" > /source-tree.txt

FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY senecio_polymarket/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY senecio_polymarket/backend ./backend
COPY senecio_polymarket/frontend ./frontend
COPY senecio_polymarket/oracle ./oracle
COPY senecio_polymarket/oracle_runtime ./oracle_runtime
COPY senecio_polymarket/start_single_authority.sh ./start_single_authority.sh
COPY senecio_polymarket/start_single_authority.sh /app/start.sh
COPY senecio_polymarket/start_single_authority.sh /start.sh

ARG NF_GIT_SHA
COPY --from=source /source-tree.txt /tmp/source-tree.txt
RUN SOURCE_TREE="$(cat /tmp/source-tree.txt)" \
 && python /app/backend/artifact_identity.py write \
      --root /app \
      --output /app/.senex-provenance/artifact-identity.json \
      --source-commit "${NF_GIT_SHA}" \
      --source-tree "${SOURCE_TREE}" \
 && rm -f /tmp/source-tree.txt
LABEL org.opencontainers.image.revision=${NF_GIT_SHA}

RUN addgroup --system --gid 10001 senex \
    && adduser --system --uid 10001 --ingroup senex --home /app --no-create-home senex \
    && mkdir -p /app/data/audit /app/oracle/senecio_output \
    && chown -R senex:senex /app/data /app/oracle/senecio_output \
    && chmod -R u=rwX,g=rX,o= /app/data /app/oracle/senecio_output \
    && chmod 0555 /app/start_single_authority.sh /app/start.sh /start.sh

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD curl -fsS http://localhost:8080/healthz || exit 1
USER senex:senex
CMD ["/app/start_single_authority.sh"]
