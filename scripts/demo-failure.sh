#!/usr/bin/env bash
# Reproducible induced-failure demo against the deployed service. Requires
# `kubectl port-forward svc/convoscore-api 8080:8000` running separately,
# and cluster access (kubectl) for the log/DB checks.
#
# Status checks query Postgres directly (the authoritative source), not the
# review page's HTML - see demo-success.sh for why grepping the page for
# "status-completed" is a false positive (it's also a CSS rule present on
# every page regardless of actual status). The DLQ check confirms *this
# run's* message specifically arrived, by depth delta plus a body match, not
# just "the DLQ happens to be nonempty" - it may already hold messages from
# earlier runs.
set -euo pipefail

NAMESPACE=${NAMESPACE:-default}
POSTGRES_POD=convoscore-postgres-0
PG_USER=convoscore
PG_DB=convoscore

API=http://localhost:8080
PROM=http://localhost:9090
PY=${PYTHON_BIN:-python3}

pg_query() {
  kubectl exec -n "$NAMESPACE" "$POSTGRES_POD" -- psql -U "$PG_USER" -d "$PG_DB" -tA -F'|' -c "$1"
}

wait_for_status() {
  local id="$1" want="$2" timeout="${3:-120}"
  local waited=0 status
  while true; do
    status=$(pg_query "SELECT status FROM conversations WHERE id='$id'")
    if [ "$status" = "$want" ]; then
      echo "  -> $id reached '$want' after ${waited}s"
      return 0
    fi
    if [ -n "$status" ] && [ "$status" != "pending" ] && [ "$status" != "processing" ] && [ "$status" != "$want" ]; then
      echo "FAILED: $id reached terminal status '$status' while waiting for '$want'" >&2
      exit 1
    fi
    waited=$((waited + 3))
    if [ "$waited" -ge "$timeout" ]; then
      echo "FAILED: timed out after ${timeout}s waiting for $id to reach '$want' (last status: '$status')" >&2
      exit 1
    fi
    sleep 3
  done
}

dlq_depth() {
  "$PY" -c "
import boto3
sqs = boto3.client('sqs', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
url = sqs.get_queue_url(QueueName='convoscore-processing-dlq')['QueueUrl']
attrs = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=['ApproximateNumberOfMessages'])
print(attrs['Attributes']['ApproximateNumberOfMessages'])
"
}

dlq_contains_key() {
  local key="$1"
  "$PY" -c "
import boto3
sqs = boto3.client('sqs', endpoint_url='http://localhost:4566', region_name='us-east-1', aws_access_key_id='test', aws_secret_access_key='test')
url = sqs.get_queue_url(QueueName='convoscore-processing-dlq')['QueueUrl']
resp = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=5, WaitTimeSeconds=2)
bodies = [m.get('Body', '') for m in resp.get('Messages', [])]
print('yes' if any('$key' in b for b in bodies) else 'no')
"
}

echo "=== DLQ baseline (before this run - may be nonzero from earlier demos) ==="
BASELINE_DEPTH=$(dlq_depth)
echo "baseline convoscore-processing-dlq depth: $BASELINE_DEPTH (not touched/purged by this script)"

echo ""
echo "=== Submitting one healthy conversation and the deterministic transient-fail trigger ==="
HEALTHY_RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"A perfectly healthy conversation submitted alongside the failure demo."}')
HEALTHY_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$HEALTHY_RESP")
echo "healthy: $HEALTHY_ID"

FAIL_RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"__TRANSIENT_FAIL__ demo-failure.sh induced failure"}')
FAIL_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$FAIL_RESP")
FAIL_SOURCE_KEY="incoming/${FAIL_ID}.json"
echo "failing:  $FAIL_ID (source_key: $FAIL_SOURCE_KEY)"

echo ""
echo "=== Waiting for the healthy conversation to complete (proves healthy traffic is unaffected) ==="
wait_for_status "$HEALTHY_ID" completed
pg_query "SELECT sentiment, risk_score, model FROM conversations WHERE id='$HEALTHY_ID'"

echo ""
echo "=== Waiting for the induced failure to exhaust retries (~105s: 3 attempts x 60s visibility timeout) ==="
wait_for_status "$FAIL_ID" failed 180
pg_query "SELECT status, error_category, error_message FROM conversations WHERE id='$FAIL_ID'"

echo ""
echo "=== Structured log evidence ==="
kubectl logs -n "$NAMESPACE" deployment/convoscore-worker --tail=200 2>&1 | grep -F "$FAIL_ID" || true

echo ""
echo "=== Metric evidence ==="
curl -s "$PROM/api/v1/query?query=conversations_processed_total" | "$PY" -m json.tool

echo ""
echo "=== Waiting for this run's message specifically to land in the DLQ ==="
echo "(depth delta over baseline, plus a body match on this conversation's source_key - not just 'DLQ is nonempty', since it may already hold messages from earlier runs)"
waited=0
FOUND=no
while [ "$waited" -lt 120 ]; do
  current=$(dlq_depth)
  if [ "$current" -gt "$BASELINE_DEPTH" ]; then
    if [ "$(dlq_contains_key "$FAIL_SOURCE_KEY")" = "yes" ]; then
      FOUND=yes
      echo "confirmed: DLQ depth $current (> baseline $BASELINE_DEPTH) and a message body matches $FAIL_SOURCE_KEY"
      break
    fi
  fi
  waited=$((waited + 10))
  sleep 10
done
if [ "$FOUND" != "yes" ]; then
  echo "FAILED: could not confirm this run's message ($FAIL_SOURCE_KEY) in the DLQ within 120s (baseline was $BASELINE_DEPTH, current: $(dlq_depth))" >&2
  exit 1
fi

echo ""
echo "=== Post-failure sanity check: submitting one more healthy conversation ==="
FINAL_RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"Post-failure sanity check."}')
FINAL_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$FINAL_RESP")
wait_for_status "$FINAL_ID" completed
echo "post-failure $FINAL_ID: completed - the worker is fully functional after the failure."

echo ""
echo "No DLQ cleanup/purge was performed by this script - the message this run added remains in the DLQ."
