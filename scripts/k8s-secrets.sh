#!/usr/bin/env bash
# Creates/updates the two Kubernetes Secrets the Helm chart references by
# name but never creates itself - see DECISIONS.md. Safe to re-run.
set -euo pipefail

if [ -f .env ]; then
  set -a
  source .env
  set +a
fi

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY is not set (checked environment and .env) - aborting." >&2
  exit 1
fi

# Local-only fixed default, same pattern as the existing local Postgres dev
# credentials used throughout this project - not a real secret at this
# scope. Override via env if you want a different one.
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-convoscore-local-dev}"
DATABASE_URL="postgresql://convoscore:${POSTGRES_PASSWORD}@convoscore-postgres:5432/convoscore"

kubectl create secret generic convoscore-openai \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic convoscore-postgres \
  --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secrets created/updated (values not printed)."
