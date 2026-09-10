#!/usr/bin/env bash
# Creates/updates the two Kubernetes Secrets the Helm chart references by
# name but never creates itself - see DECISIONS.md. Safe to re-run.
set -euo pipefail

NAMESPACE=${NAMESPACE:-default}
PY=${PYTHON_BIN:-python3}

if [ ! -f .env ]; then
  echo ".env not found - copy .env.example to .env and set OPENAI_API_KEY first." >&2
  exit 1
fi

set -a
source .env
set +a

if [ -z "${OPENAI_API_KEY:-}" ]; then
  echo "OPENAI_API_KEY is not set (checked environment and .env) - aborting." >&2
  exit 1
fi

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  # No fixed fallback password as the normal path - generate a strong one
  # once and persist it only in the gitignored .env, so a fresh clone never
  # ships (or silently reuses) a known local password. See DECISIONS.md.
  POSTGRES_PASSWORD=$("$PY" -c "import secrets; print(secrets.token_urlsafe(24))")
  {
    echo ""
    echo "# Generated once by scripts/k8s-secrets.sh - do not commit"
    echo "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}"
  } >> .env
  echo "Generated a new POSTGRES_PASSWORD and saved it to .env (value not printed)."
fi

# Build the connection string with the password percent-encoded, so a
# generated password containing URI-reserved characters (":", "@", "/", "%",
# etc.) can never produce a malformed or misparsed DATABASE_URL.
DATABASE_URL=$(PGPW="$POSTGRES_PASSWORD" "$PY" -c "
import os, urllib.parse
pw = urllib.parse.quote(os.environ['PGPW'], safe='')
print(f'postgresql://convoscore:{pw}@convoscore-postgres:5432/convoscore')
")

kubectl create secret generic convoscore-openai \
  -n "$NAMESPACE" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic convoscore-postgres \
  -n "$NAMESPACE" \
  --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Secrets created/updated in namespace '$NAMESPACE' (values not printed)."
