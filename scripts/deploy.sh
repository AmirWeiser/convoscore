#!/usr/bin/env bash
# Builds, tags, loads, and deploys the current git commit's code to minikube.
# The image tag is never hardcoded anywhere (chart enforces this with
# `required`) - it is always the live git SHA at the moment this runs, so
# what's deployed is always traceable to an exact commit. Safe to re-run.
set -euo pipefail

NAMESPACE=${NAMESPACE:-default}

SHA=$(git rev-parse --short HEAD)
echo "Deploying commit $SHA to namespace '$NAMESPACE'"

# A dirty tracked file (or a new, not-yet-committed one under these paths)
# would let `docker build .` produce an image whose actual contents don't
# match what the $SHA tag claims - the same tag could then mean two
# different images depending on when it was built. Fail before building,
# not after. Gitignored runtime files (.env, terraform state, __pycache__,
# .venv) are excluded automatically since `git status` never reports them.
DIRTY=$(git status --porcelain -- app Dockerfile charts terraform/*.tf requirements.txt requirements-dev.txt scripts)
if [ -n "$DIRTY" ]; then
  echo "Refusing to deploy: uncommitted changes under tracked source/Docker/Helm/Terraform/requirements/scripts:" >&2
  echo "$DIRTY" >&2
  echo "Commit or stash them first - the image tag must exactly match what's committed." >&2
  exit 1
fi

docker build -t "convoscore:$SHA" .
minikube image load "convoscore:$SHA"

NAMESPACE="$NAMESPACE" bash scripts/k8s-secrets.sh

helm upgrade --install convoscore charts/convoscore \
  --namespace "$NAMESPACE" --create-namespace \
  --set image.tag="$SHA"

kubectl rollout status -n "$NAMESPACE" deployment/convoscore-api --timeout=120s
kubectl rollout status -n "$NAMESPACE" deployment/convoscore-worker --timeout=120s

echo "Deployed convoscore:$SHA to namespace '$NAMESPACE'"
