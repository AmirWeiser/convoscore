#!/usr/bin/env bash
# Reproducible restart/durability demo. Requires kubectl access to the
# cluster; does NOT require a port-forward for the pod-deletion parts.
set -euo pipefail

PY=${PYTHON_BIN:-python3}

EXPECTED_TAG=$(git rev-parse --short HEAD)
echo "Expected image tag (current git SHA): $EXPECTED_TAG"

echo ""
echo "=== Submitting one conversation and waiting for it to complete, before the restart ==="
kubectl port-forward svc/convoscore-api 8080:8000 > /tmp/demo-restart-pf.log 2>&1 &
PF_PID=$!
sleep 3
RESP=$(curl -s -X POST http://localhost:8080/conversations -H "Content-Type: application/json" \
  -d '{"text":"Restart demo: this result must survive pod deletion."}')
ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$RESP")
until curl -s "http://localhost:8080/conversations/$ID" | grep -q 'status-completed'; do sleep 3; done
echo "$ID: completed, stored before restart"
kill "$PF_PID" 2>/dev/null || true

echo ""
echo "=== Deleting API and worker pods (their Deployments will recreate them) ==="
kubectl delete pod -l app.kubernetes.io/component=api --wait=false
kubectl delete pod -l app.kubernetes.io/component=worker --wait=false
kubectl rollout status deployment/convoscore-api --timeout=120s
kubectl rollout status deployment/convoscore-worker --timeout=120s

echo ""
echo "=== Confirming recreated pods use the expected image tag ==="
ACTUAL_API_TAG=$(kubectl get deployment convoscore-api -o jsonpath='{.spec.template.spec.containers[0].image}')
ACTUAL_WORKER_TAG=$(kubectl get deployment convoscore-worker -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "api:    $ACTUAL_API_TAG"
echo "worker: $ACTUAL_WORKER_TAG"
echo "$ACTUAL_API_TAG" | grep -q "$EXPECTED_TAG" && echo "api tag OK"
echo "$ACTUAL_WORKER_TAG" | grep -q "$EXPECTED_TAG" && echo "worker tag OK"

echo ""
echo "=== Deleting the Postgres pod ONLY (PVC is untouched) - StatefulSet recreates it ==="
kubectl delete pod convoscore-postgres-0 --wait=false
kubectl rollout status statefulset/convoscore-postgres --timeout=120s

echo ""
echo "=== Confirming the pre-restart result still exists ==="
kubectl port-forward svc/convoscore-api 8080:8000 > /tmp/demo-restart-pf2.log 2>&1 &
PF_PID=$!
# Poll for the terminal 'completed' state specifically, not just any status
# span. Deleting the Postgres pod mid-flight can interrupt an in-flight SQS
# delivery (AdminShutdown on the connection); when that happens SQS's normal
# at-least-once redelivery reprocesses the same conversation once more before
# it lands back on 'completed'. That is a real, expected edge case of this
# design (see DECISIONS.md) - not data loss - so give it a generous window
# to converge instead of only accepting an immediate match.
STATUS_LINE=""
for _ in $(seq 1 40); do
  STATUS_LINE=$(curl -s "http://localhost:8080/conversations/$ID" | grep -o '<span class="status status-[a-z_]*">[a-z_]*</span>' || true)
  echo "$STATUS_LINE" | grep -q 'status-completed' && break
  sleep 3
done
kill "$PF_PID" 2>/dev/null || true
if [ -z "$STATUS_LINE" ]; then
  echo "FAILED: could not confirm post-restart status for $ID (port-forward or API never became ready)"
  exit 1
fi
echo "$STATUS_LINE"
if echo "$STATUS_LINE" | grep -q 'status-completed'; then
  echo "CONFIRMED: result survived API/worker/Postgres pod deletion"
else
  echo "FAILED: $ID did not reach completed within the poll window (last seen: $STATUS_LINE)"
  exit 1
fi

echo ""
echo "Restart demo complete. Note: this proves pod-restart durability via the"
echo "PVC, not survival of 'minikube delete' or full cluster teardown - the"
echo "PVC only survives as long as the underlying volume does. It may also"
echo "reprocess the demo conversation a second time if the Postgres restart"
echo "interrupts an in-flight SQS delivery - SQS's normal at-least-once"
echo "redelivery, self-healing to the correct final state, not a bug."
