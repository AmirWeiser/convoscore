#!/usr/bin/env bash
# Reproducible success demo against the deployed service. Requires
# `kubectl port-forward svc/convoscore-api 8080:8000` running separately.
# Uses python3 (standard on Linux/Mac; on Windows this may need to be `py`
# depending on local setup - see README).
#
# Status checks query Postgres directly (the authoritative source), not the
# review page's HTML: the page's <style> block always contains the literal
# string "status-completed" as a CSS rule, regardless of the conversation's
# actual current status, so grepping the raw page for that string is a false
# positive waiting to happen. See DECISIONS.md.
set -euo pipefail

NAMESPACE=${NAMESPACE:-default}
POSTGRES_POD=convoscore-postgres-0
PG_USER=convoscore
PG_DB=convoscore

API=http://localhost:8080
PY=${PYTHON_BIN:-python3}

pg_query() {
  kubectl exec -n "$NAMESPACE" "$POSTGRES_POD" -- psql -U "$PG_USER" -d "$PG_DB" -tA -F'|' -c "$1"
}

# Polls until the row (looked up by id or source_key) reaches the given
# terminal status. Fails loudly - not silently - on timeout or on reaching a
# *different* terminal status than expected.
wait_for_status() {
  local by="$1" value="$2" want="$3" timeout="${4:-90}"
  local column=$([ "$by" = "id" ] && echo "id" || echo "source_key")
  local waited=0 status
  while true; do
    status=$(pg_query "SELECT status FROM conversations WHERE $column='$value'")
    if [ "$status" = "$want" ]; then
      echo "  -> reached '$want' after ${waited}s"
      return 0
    fi
    if [ -n "$status" ] && [ "$status" != "pending" ] && [ "$status" != "processing" ] && [ "$status" != "$want" ]; then
      echo "FAILED: $column=$value reached terminal status '$status' while waiting for '$want'" >&2
      pg_query "SELECT error_category, error_message FROM conversations WHERE $column='$value'" >&2
      exit 1
    fi
    waited=$((waited + 3))
    if [ "$waited" -ge "$timeout" ]; then
      echo "FAILED: timed out after ${timeout}s waiting for $column=$value to reach '$want' (last status: '$status')" >&2
      exit 1
    fi
    sleep 3
  done
}

echo "=== Direct API ingestion ==="
RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"Demo: customer is happy with the fast support response."}')
echo "$RESP"
API_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$RESP")

echo ""
echo "=== Direct S3 ingestion (no API call) ==="
S3_KEY=$("$PY" -c "
import boto3, json, uuid
s3 = boto3.client('s3', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
key = f'incoming/demo-s3-{uuid.uuid4()}.json'
s3.put_object(Bucket='convoscore-conversations', Key=key, Body=json.dumps({'text': 'Demo: customer reports the checkout page is much faster now.'}))
print(key)
")
echo "uploaded $S3_KEY"

echo ""
echo "=== Waiting for the API-submitted conversation to complete (real OpenAI scoring) ==="
wait_for_status id "$API_ID" completed
echo "API-submitted $API_ID: completed"
pg_query "SELECT sentiment, risk_score, model, input_tokens, output_tokens, estimated_cost_usd FROM conversations WHERE id='$API_ID'"

echo ""
echo "=== Waiting for the S3-submitted conversation to complete (real OpenAI scoring) ==="
wait_for_status source_key "$S3_KEY" completed
echo "S3-submitted (source_key: $S3_KEY): completed"
pg_query "SELECT sentiment, risk_score, model, input_tokens, output_tokens, estimated_cost_usd FROM conversations WHERE source_key='$S3_KEY'"

echo ""
echo "Done. Both ingestion paths reached 'completed' with real OpenAI results, confirmed against Postgres directly."
