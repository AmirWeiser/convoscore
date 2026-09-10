#!/usr/bin/env bash
# Builds, tags, loads, and deploys the current git commit's code to minikube.
# The image tag is never hardcoded anywhere (chart enforces this with
# `required`) - it is always the live git SHA at the moment this runs, so
# what's deployed is always traceable to an exact commit. Safe to re-run.
set -euo pipefail

SHA=$(git rev-parse --short HEAD)
echo "Deploying commit $SHA"

docker build -t "convoscore:$SHA" .
minikube image load "convoscore:$SHA"

bash scripts/k8s-secrets.sh

helm upgrade --install convoscore charts/convoscore --set image.tag="$SHA"

kubectl rollout status deployment/convoscore-api --timeout=120s
kubectl rollout status deployment/convoscore-worker --timeout=120s

echo "Deployed convoscore:$SHA"
