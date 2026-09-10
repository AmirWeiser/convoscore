#!/usr/bin/env bash
# Reproducible induced-failure demo against the deployed service. Requires
# `kubectl port-forward svc/convoscore-api 8080:8000` running separately,
# and cluster access (kubectl) for the log/metric checks.
set -euo pipefail

API=http://localhost:8080
PY=${PYTHON_BIN:-python3}

echo "=== Submitting one healthy conversation and the deterministic transient-fail trigger ==="
HEALTHY_RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"A perfectly healthy conversation submitted alongside the failure demo."}')
HEALTHY_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$HEALTHY_RESP")
echo "healthy: $HEALTHY_ID"

FAIL_RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"__TRANSIENT_FAIL__ demo-failure.sh induced failure"}')
FAIL_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$FAIL_RESP")
echo "failing:  $FAIL_ID"

echo ""
echo "=== Waiting for the healthy conversation to complete (proves healthy traffic is unaffected) ==="
until curl -s "$API/conversations/$HEALTHY_ID" | grep -q 'status-completed'; do sleep 3; done
echo "healthy $HEALTHY_ID: completed"

echo ""
echo "=== Waiting for the induced failure to exhaust retries (~105s: 3 attempts x 60s visibility timeout) ==="
until curl -s "$API/conversations/$FAIL_ID" | grep -q '<span class="status status-failed">'; do sleep 5; done
echo "failing $FAIL_ID: failed (retries exhausted)"

echo ""
echo "=== Structured log evidence ==="
kubectl logs deployment/convoscore-worker --tail=200 2>&1 | grep "$FAIL_ID" || true

echo ""
echo "=== Metric evidence ==="
curl -s "http://localhost:9090/api/v1/query?query=conversations_processed_total" | "$PY" -m json.tool
echo ""
echo "Waiting for the message to land in the DLQ (one more visibility-timeout cycle)..."
until [ "$(curl -s 'http://localhost:9090/api/v1/query?query=convoscore_dlq_depth' | "$PY" -c "import sys,json;d=json.load(sys.stdin);print(int(max([float(r['value'][1]) for r in d['data']['result']], default=0)))")" != "0" ]; do
  sleep 10
done
echo "convoscore_dlq_depth is now nonzero - DLQ confirmed."

echo ""
echo "=== Post-failure sanity check: submitting one more healthy conversation ==="
FINAL_RESP=$(curl -s -X POST "$API/conversations" -H "Content-Type: application/json" \
  -d '{"text":"Post-failure sanity check."}')
FINAL_ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$FINAL_RESP")
until curl -s "$API/conversations/$FINAL_ID" | grep -q 'status-completed'; do sleep 3; done
echo "post-failure $FINAL_ID: completed - the worker is fully functional after the failure."
