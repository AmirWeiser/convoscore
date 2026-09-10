# ConvoScore

LLM-scored support conversations, end to end: API/S3 ingestion → SQS → worker → OpenAI
→ Postgres → human review UI, deployed to Kubernetes with Prometheus/Grafana
observability. Architecture and design tradeoffs are in `DECISIONS.md`.

## Prerequisites

- Docker (the docker driver is used for minikube, and runs LocalStack + the local dev
  Postgres as containers)
- [minikube](https://minikube.sigs.k8s.io/), kubectl, [Helm](https://helm.sh/) 3
- [Terraform](https://developer.hashicorp.com/terraform) >= 1.9
- Python 3.14 (only needed to run the test suite or the demo scripts locally - not
  needed just to deploy)
- An OpenAI API key with access to `gpt-4o-mini` (or set `OPENAI_MODEL` to another
  model your key can use)

Everything below assumes commands run from the repo root, and that `kubectl config
current-context` is `minikube` - if you have other clusters configured, double-check
before running anything that deletes resources.

## 1. Secrets

```
cp .env.example .env
# edit .env and set OPENAI_API_KEY=sk-...
```

`.env` is only read locally (by `scripts/k8s-secrets.sh` and, if you run the app outside
Kubernetes, by `app/config.py`). It is gitignored and never templated into the Helm
chart - the chart only references two Kubernetes Secrets by name, created out-of-band.

## 2. Start local infrastructure

```
docker run -d --name convoscore-localstack -p 4566:4566 localstack/localstack:3.8
minikube start --driver=docker   # skip if already running
```

## 3. Provision AWS resources (LocalStack) with Terraform

```
cd terraform
terraform init
terraform apply     # add -auto-approve to skip the confirmation prompt
cd ..
```

Creates the S3 bucket, the processing queue + DLQ, the S3→SQS bucket notification, and
the IAM policies describing the intended least-privilege access (see `DECISIONS.md` for
why LocalStack doesn't actually enforce that last part).

## 4. Deploy to Kubernetes

```
bash scripts/deploy.sh
```

This one script: builds the Docker image tagged with the current git commit SHA, loads
it into minikube, creates/updates the two Kubernetes Secrets from `.env`
(`scripts/k8s-secrets.sh`), and runs `helm upgrade --install`. Safe to re-run any time
you change code - re-run it to redeploy.

```
kubectl get pods
```

should show `convoscore-api`, `convoscore-worker`, `convoscore-postgres-0`,
`convoscore-prometheus`, and `convoscore-grafana` all `1/1 Running`.

## UI access

Everything is `ClusterIP` - reach it via `kubectl port-forward`, one per terminal (or
backgrounded):

| Service | Command | URL |
|---|---|---|
| API + review UI | `kubectl port-forward svc/convoscore-api 8080:8000` | http://localhost:8080/conversations |
| Grafana | `kubectl port-forward svc/convoscore-grafana 3000:3000` | http://localhost:3000 |
| Prometheus | `kubectl port-forward svc/convoscore-prometheus 9090:9090` | http://localhost:9090 |

The review UI lists recent conversations and their status; click one for the full
scored result. `/healthz` and `/readyz` are plain JSON. `/metrics` is the API's own
Prometheus endpoint. Grafana uses anonymous admin access (a documented local-demo
tradeoff, not a production auth setup - see `DECISIONS.md`) and comes with one
dashboard preloaded: processing rate, failures/DLQ depth, latency p95, OpenAI token
usage & cost.

Submit a conversation directly:

```
curl -X POST http://localhost:8080/conversations -H "Content-Type: application/json" \
  -d '{"text":"The customer was frustrated but the issue got resolved quickly."}'
```

## Demos

Reproducible scripts in `scripts/`, run against the real deployed cluster (real OpenAI
calls - no `SCORER_PROVIDER=fake` in Kubernetes). All of them talk to Postgres directly
via `kubectl exec` for authoritative status checks, not the review page's HTML.

| Script | Needs | Proves |
|---|---|---|
| `demo-success.sh` | API port-forward (8080) | Both ingestion paths (API and direct S3 upload) reach `completed` with real OpenAI results |
| `demo-failure.sh` | API port-forward (8080) + Prometheus port-forward (9090) | An induced deterministic failure retries 3x, reaches `failed`/`transient_exhausted`, and lands in the DLQ (confirmed by depth delta + a body match, not just "DLQ is nonempty"); healthy conversations keep completing throughout |
| `demo-restart.sh` | none - manages its own port-forward | API/worker/Postgres pods are deleted and recreated (new pod UIDs, `Ready`, correct image tag); the stored result for a conversation submitted before the restart survives **byte-for-byte identical**, including `completed_at` - or the script exits nonzero |

```
PYTHON_BIN=python3 bash scripts/demo-success.sh   # PYTHON_BIN: point at a real
                                                    # interpreter if `python3` doesn't
                                                    # resolve to one on your system
```

`demo-failure.sh` submits `"__TRANSIENT_FAIL__ ..."` as the conversation text - a
deterministic trigger honored by both the fake and real scorer, so the failure/DLQ
path doesn't depend on hoping for a real OpenAI error. It takes a few minutes (three
real ~60s SQS visibility-timeout cycles).

## Running the test suite locally

The test suite runs outside Kubernetes against a local Postgres (not the one in the
cluster):

```
docker run -d --name convoscore-postgres -p 5432:5432 \
  -e POSTGRES_USER=convoscore -e POSTGRES_PASSWORD=convoscore -e POSTGRES_DB=convoscore \
  postgres:16-alpine

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # Windows: .venv\Scripts\pip
.venv/bin/python -m pytest tests/ -v                                # Windows: .venv\Scripts\python
```

Tests use `FakeScorer` (deterministic, no network calls) and never hit the real OpenAI
API.

## Teardown

Scoped to ConvoScore's own resources only - nothing here touches other projects,
clusters, or containers.

```
helm uninstall convoscore                 # removes the Deployments/Services/etc.
kubectl delete pvc data-convoscore-postgres-0   # deletes the Postgres data - irreversible
cd terraform && terraform destroy && cd .. # removes the S3 bucket/SQS queues/IAM in LocalStack
docker rm -f convoscore-localstack         # stops and removes the LocalStack container - deletes its data
docker rm -f convoscore-postgres           # only if you started the local dev Postgres above - deletes its data
minikube stop                              # or `minikube delete` to remove the cluster entirely
```

`helm uninstall` alone leaves the Postgres PVC and its data behind (deliberately - see
`DECISIONS.md` on restart durability); the `kubectl delete pvc` step above is what
actually deletes the scored-conversation data, and is the only irreversible step here
by design.
