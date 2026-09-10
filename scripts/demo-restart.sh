#!/usr/bin/env bash
# Reproducible restart/durability demo. Requires kubectl access to the
# cluster; does NOT require a port-forward for the pod-deletion parts.
#
# Status/result checks query Postgres directly (the authoritative source),
# not the review page's HTML - see demo-success.sh for why grepping the page
# for "status-completed" is a false positive (it's also a CSS rule present
# on every page regardless of actual status).
#
# "Survives" is proven by comparing the exact result row (including
# completed_at) before and after, not by seeing 'completed' again - a fresh
# completed_at would mean the conversation was re-scored, not that the
# original result survived.
#
# Pod recreation is proven by comparing pod UIDs (a genuinely new object,
# not the same pod reporting Ready again) and Ready conditions, and the
# image tag is compared against what was *actually running* before the
# restart (read from the live pod, not assumed from git HEAD - the two can
# drift if the working tree had uncommitted changes at deploy time).
set -euo pipefail

NAMESPACE=${NAMESPACE:-default}
POSTGRES_POD=convoscore-postgres-0
PG_USER=convoscore
PG_DB=convoscore
SELECTOR_BASE="app.kubernetes.io/name=convoscore"

PY=${PYTHON_BIN:-python3}

pg_query() {
  kubectl exec -n "$NAMESPACE" "$POSTGRES_POD" -- psql -U "$PG_USER" -d "$PG_DB" -tA -F'|' -c "$1"
}

wait_for_status() {
  local id="$1" want="$2" timeout="${3:-90}"
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

# Full result snapshot, including completed_at - any change proves
# re-processing happened, not just "status is completed again".
result_snapshot() {
  pg_query "SELECT id,sentiment,risk_score,rationale,model,prompt_version,input_tokens,output_tokens,estimated_cost_usd,completed_at FROM conversations WHERE id='$1'"
}

pod_uid() {
  kubectl get pod -n "$NAMESPACE" "$1" -o jsonpath='{.metadata.uid}' 2>/dev/null
}

pod_image() {
  kubectl get pod -n "$NAMESPACE" "$1" -o jsonpath='{.spec.containers[0].image}' 2>/dev/null
}

selector_pod_name() {
  kubectl get pod -n "$NAMESPACE" -l "$SELECTOR_BASE,app.kubernetes.io/component=$1" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null
}

GIT_HEAD=$(git rev-parse --short HEAD)
echo "git HEAD: $GIT_HEAD (informational only - the actual comparison below uses the tag genuinely running before the restart, in case the tree was dirty at deploy time)"

echo ""
echo "=== Submitting one conversation and waiting for it to complete, before the restart ==="
kubectl port-forward -n "$NAMESPACE" svc/convoscore-api 8080:8000 > /tmp/demo-restart-pf.log 2>&1 &
PF_PID=$!
sleep 3
RESP=$(curl -s -X POST http://localhost:8080/conversations -H "Content-Type: application/json" \
  -d '{"text":"Restart demo: this result must survive pod deletion."}')
ID=$("$PY" -c "import sys,json;print(json.load(sys.stdin)['id'])" <<< "$RESP")
kill "$PF_PID" 2>/dev/null || true
wait_for_status "$ID" completed
BEFORE_SNAPSHOT=$(result_snapshot "$ID")
echo "$ID: completed, stored before restart"
echo "before: $BEFORE_SNAPSHOT"

echo ""
echo "=== Capturing pre-deletion pod identity (UID + actually-running image) ==="
API_POD_BEFORE=$(selector_pod_name api)
WORKER_POD_BEFORE=$(selector_pod_name worker)
API_UID_BEFORE=$(pod_uid "$API_POD_BEFORE")
WORKER_UID_BEFORE=$(pod_uid "$WORKER_POD_BEFORE")
EXPECTED_TAG=$(pod_image "$API_POD_BEFORE")
WORKER_TAG_BEFORE=$(pod_image "$WORKER_POD_BEFORE")
echo "api:    $API_POD_BEFORE (uid $API_UID_BEFORE) image $EXPECTED_TAG"
echo "worker: $WORKER_POD_BEFORE (uid $WORKER_UID_BEFORE) image $WORKER_TAG_BEFORE"
if [ "$EXPECTED_TAG" != "$WORKER_TAG_BEFORE" ]; then
  echo "FAILED: api and worker are running different images before the restart ($EXPECTED_TAG vs $WORKER_TAG_BEFORE)" >&2
  exit 1
fi
POSTGRES_UID_BEFORE=$(pod_uid "$POSTGRES_POD")
echo "postgres: $POSTGRES_POD (uid $POSTGRES_UID_BEFORE)"

echo ""
echo "=== Deleting API and worker pods (narrowly selected, namespace $NAMESPACE - their Deployments recreate them) ==="
kubectl delete pod -n "$NAMESPACE" -l "$SELECTOR_BASE,app.kubernetes.io/component=api" --wait=false
kubectl delete pod -n "$NAMESPACE" -l "$SELECTOR_BASE,app.kubernetes.io/component=worker" --wait=false
kubectl rollout status -n "$NAMESPACE" deployment/convoscore-api --timeout=120s
kubectl rollout status -n "$NAMESPACE" deployment/convoscore-worker --timeout=120s

echo ""
echo "=== Verifying the recreated pods are genuinely new objects, Ready, and on the expected image ==="
API_POD_AFTER=$(selector_pod_name api)
WORKER_POD_AFTER=$(selector_pod_name worker)
kubectl wait -n "$NAMESPACE" --for=condition=Ready "pod/$API_POD_AFTER" --timeout=120s
kubectl wait -n "$NAMESPACE" --for=condition=Ready "pod/$WORKER_POD_AFTER" --timeout=120s
API_UID_AFTER=$(pod_uid "$API_POD_AFTER")
WORKER_UID_AFTER=$(pod_uid "$WORKER_POD_AFTER")
API_TAG_AFTER=$(pod_image "$API_POD_AFTER")
WORKER_TAG_AFTER=$(pod_image "$WORKER_POD_AFTER")
echo "api:    $API_POD_AFTER (uid $API_UID_AFTER) image $API_TAG_AFTER"
echo "worker: $WORKER_POD_AFTER (uid $WORKER_UID_AFTER) image $WORKER_TAG_AFTER"

FAIL=0
[ "$API_UID_AFTER" = "$API_UID_BEFORE" ] && { echo "FAILED: api pod UID unchanged - not actually recreated" >&2; FAIL=1; }
[ "$WORKER_UID_AFTER" = "$WORKER_UID_BEFORE" ] && { echo "FAILED: worker pod UID unchanged - not actually recreated" >&2; FAIL=1; }
[ "$API_TAG_AFTER" != "$EXPECTED_TAG" ] && { echo "FAILED: api image is $API_TAG_AFTER, expected $EXPECTED_TAG" >&2; FAIL=1; }
[ "$WORKER_TAG_AFTER" != "$EXPECTED_TAG" ] && { echo "FAILED: worker image is $WORKER_TAG_AFTER, expected $EXPECTED_TAG" >&2; FAIL=1; }
[ "$FAIL" = "1" ] && exit 1
echo "api and worker: new pod UIDs, Ready, image tag matches the pre-restart deployed version ($EXPECTED_TAG)"

echo ""
echo "=== Deleting the Postgres pod ONLY by exact name (PVC is untouched) - StatefulSet recreates it ==="
kubectl delete pod -n "$NAMESPACE" "$POSTGRES_POD" --wait=false
kubectl rollout status -n "$NAMESPACE" statefulset/convoscore-postgres --timeout=120s
kubectl wait -n "$NAMESPACE" --for=condition=Ready "pod/$POSTGRES_POD" --timeout=120s
POSTGRES_UID_AFTER=$(pod_uid "$POSTGRES_POD")
echo "postgres: $POSTGRES_POD (uid $POSTGRES_UID_AFTER)"
if [ "$POSTGRES_UID_AFTER" = "$POSTGRES_UID_BEFORE" ]; then
  echo "FAILED: postgres pod UID unchanged - not actually recreated" >&2
  exit 1
fi
echo "postgres: new pod UID, Ready"

echo ""
echo "=== Comparing the stored result before vs. after the restart ==="
AFTER_SNAPSHOT=$(result_snapshot "$ID")
echo "before: $BEFORE_SNAPSHOT"
echo "after:  $AFTER_SNAPSHOT"
if [ "$BEFORE_SNAPSHOT" = "$AFTER_SNAPSHOT" ]; then
  echo "CONFIRMED: the exact result (including completed_at) survived unchanged - not re-processed."
else
  echo "FAILED: the result differs after the restart - this means the conversation was re-scored" >&2
  echo "(a new completed_at and/or different result fields), not that the original result was proven" >&2
  echo "to survive. A passing run must show byte-for-byte identical rows; treat this as a real finding" >&2
  echo "to investigate (e.g. app/worker.py's claim-vs-delete handling), not an expected outcome." >&2
  exit 1
fi

echo ""
echo "Restart demo complete. Note: this proves pod-restart durability via the"
echo "PVC, not survival of 'minikube delete' or full cluster teardown - the"
echo "PVC only survives as long as the underlying volume does."
