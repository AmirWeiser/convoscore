#!/usr/bin/env bash
# Complete, repeatable teardown of ConvoScore's own resources only. Safe to
# run repeatedly - every step tolerates "already gone". Never touches
# minikube itself (the cluster may be shared with another project) and
# never touches any resource outside ConvoScore's own naming.
#
# Order matters: Terraform needs LocalStack still running to destroy the
# S3/SQS/IAM resources it created there, so Kubernetes/Secrets/PVC cleanup
# happens first, then `terraform destroy`, and only then is the LocalStack
# container itself removed.
set -euo pipefail

NAMESPACE=${NAMESPACE:-default}

CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")
if [ "$CURRENT_CONTEXT" != "minikube" ]; then
  echo "Refusing to run: current kubectl context is '$CURRENT_CONTEXT', not 'minikube'." >&2
  echo "This script only ever touches the minikube cluster's ConvoScore resources - switch context first if that's really what you intend." >&2
  exit 1
fi

echo "=== Removing the Helm release (namespace: $NAMESPACE) ==="
helm uninstall convoscore -n "$NAMESPACE" || echo "(already removed, or nothing to remove)"

echo "=== Deleting the Postgres PVC - this permanently deletes stored conversation data ==="
kubectl delete pvc -n "$NAMESPACE" data-convoscore-postgres-0 --ignore-not-found

echo "=== Deleting the two out-of-band Kubernetes Secrets ==="
kubectl delete secret -n "$NAMESPACE" convoscore-openai convoscore-postgres --ignore-not-found

echo "=== Destroying Terraform-managed LocalStack resources (S3/SQS/IAM) ==="
if docker ps --format '{{.Names}}' | grep -qx convoscore-localstack; then
  (cd "$(dirname "$0")/../terraform" && terraform destroy -auto-approve) || \
    echo "terraform destroy failed or nothing to destroy - continuing." >&2
else
  echo "convoscore-localstack is not running - skipping terraform destroy (nothing to talk to)." >&2
fi

echo "=== Removing the LocalStack container - this permanently deletes its S3/SQS data too ==="
docker rm -f convoscore-localstack >/dev/null 2>&1 || true

echo ""
echo "Teardown complete. Not touched: minikube itself (stop/delete it separately"
echo "and only if you're sure no other project is using this cluster - see README),"
echo "and the optional local dev Postgres container used only for running tests"
echo "outside Kubernetes (docker rm -f convoscore-postgres, if you started one)."
