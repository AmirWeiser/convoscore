#!/usr/bin/env bash
# Complete, repeatable teardown of ConvoScore's own resources only. Safe to
# run repeatedly - every step tolerates "already gone". Never touches
# minikube itself (the cluster may be shared with another project) and
# never touches any resource outside ConvoScore's own naming.
#
# Order matters: Terraform needs LocalStack still running to destroy the
# S3/SQS/IAM resources it created there, so Kubernetes/Secrets/PVC cleanup
# happens first, then `terraform destroy`, and only then is the LocalStack
# container itself removed - and only if that destroy actually succeeded
# (or there was definitively nothing to destroy). See
# destroy_terraform_and_localstack() below and
# tests/test_teardown_destroy_safety.sh.
set -euo pipefail

# "Definitively no state to destroy" means an empty or absent Terraform
# state - not merely LocalStack being unreachable. A failed destroy (or a
# nonempty state we can't reach) must abort here and leave LocalStack
# alone: removing it in that case wouldn't destroy the resources, only make
# them unreachable. Takes the terraform directory as $1 so this is testable
# in isolation (tests/test_teardown_destroy_safety.sh sources this file and
# calls the function directly with stubbed `terraform`/`docker` on PATH).
destroy_terraform_and_localstack() {
  local tf_dir="$1"

  echo "=== Destroying Terraform-managed LocalStack resources (S3/SQS/IAM) ==="
  local has_managed_resources=false
  if [ -d "$tf_dir/.terraform" ] && [ -f "$tf_dir/terraform.tfstate" ]; then
    local resource_count
    resource_count=$(cd "$tf_dir" && terraform state list 2>/dev/null | wc -l) || resource_count=0
    [ "$resource_count" -gt 0 ] && has_managed_resources=true
  fi

  if [ "$has_managed_resources" = "true" ]; then
    if ! docker ps --format '{{.Names}}' | grep -qx convoscore-localstack; then
      echo "Terraform state has managed resources but convoscore-localstack is not running -" >&2
      echo "cannot destroy them, and refusing to remove the LocalStack container (that would" >&2
      echo "make the resources unreachable, not actually destroy them). Start" >&2
      echo "convoscore-localstack and re-run this script." >&2
      return 1
    fi
    # No fallback on failure - the caller must abort (set -e in the normal
    # script path) and LocalStack must never be reached if this fails.
    (cd "$tf_dir" && terraform destroy -auto-approve)
    echo "Terraform destroy succeeded."
  else
    echo "No Terraform-managed resources to destroy (state absent or empty)."
  fi

  echo "=== Removing the LocalStack container - this permanently deletes its S3/SQS data too ==="
  docker rm -f convoscore-localstack >/dev/null 2>&1 || true
}

# Allows tests to `source` this file (to reuse destroy_terraform_and_localstack
# in isolation) without running the rest of the script.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
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

  TF_DIR="$(cd "$(dirname "$0")/../terraform" && pwd)"
  destroy_terraform_and_localstack "$TF_DIR"

  echo ""
  echo "Teardown complete. Not touched: minikube itself (stop/delete it separately"
  echo "and only if you're sure no other project is using this cluster - see README),"
  echo "and the optional local dev Postgres container used only for running tests"
  echo "outside Kubernetes (docker rm -f convoscore-postgres, if you started one)."
fi
