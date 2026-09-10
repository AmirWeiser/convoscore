# ConvoScore

LLM-scored support conversations, end to end: API/S3 ingestion → SQS → worker → OpenAI
→ Postgres → human review UI, deployed to Kubernetes with Prometheus/Grafana
observability. Architecture and design tradeoffs are in `DECISIONS.md`.

## Prerequisites

- Docker (runs minikube's docker driver, plus LocalStack and the local dev Postgres as
  plain containers)
- [minikube](https://minikube.sigs.k8s.io/) (tested with v1.35), kubectl, [Helm](https://helm.sh/) 3
- [Terraform](https://developer.hashicorp.com/terraform) >= 1.9
- Python 3.11+ (tested with 3.14) - only needed to run the test suite or the demo
  scripts locally, not needed just to deploy
- An OpenAI API key with access to `gpt-4o-mini` (or set `scoring.model`/`OPENAI_MODEL`
  to another model your key can use - see "Configuration" below)

Everything below assumes commands run from the repo root, and that `kubectl config
current-context` is `minikube` - if you have other clusters configured, double-check
before running anything that deletes resources. `scripts/teardown.sh` refuses to run
if the current context isn't `minikube`, but the other scripts don't check.

```
git clone https://github.com/AmirWeiser/convoscore.git
cd convoscore
```

## 1. Secrets

```
cp .env.example .env
# edit .env and set OPENAI_API_KEY=sk-...
```

`.env` is only read locally (by `scripts/k8s-secrets.sh` and, if you run the app outside
Kubernetes, by `app/config.py`). It is gitignored and never templated into the Helm
chart - the chart only references two Kubernetes Secrets by name, created out-of-band.
`POSTGRES_PASSWORD` does not need to be set - `scripts/k8s-secrets.sh` generates a
strong one on first run and appends it to `.env` so it stays fixed across redeploys.

## 2. Local Python environment

Needed for both the demo scripts (they import `boto3`) and the test suite - set this up
once, before running either:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt   # Windows: .venv\Scripts\pip
```

## 3. Start local infrastructure

```
docker run -d --name convoscore-localstack -p 4566:4566 localstack/localstack:3.8
minikube start --driver=docker   # skip if already running
```

## 4. Provision AWS resources (LocalStack) with Terraform

```
cd terraform
terraform init
terraform apply     # add -auto-approve to skip the confirmation prompt
cd ..
```

Creates the S3 bucket, the processing queue + DLQ, the S3→SQS bucket notification, and
the IAM policies describing the intended least-privilege access (see `DECISIONS.md` for
why LocalStack doesn't actually enforce that last part).

## 5. Deploy to Kubernetes

```
bash scripts/deploy.sh
```

This one script: refuses to run if any tracked source/Docker/Helm/Terraform/
requirements/script file has uncommitted changes (the image tag is the git SHA, so a
dirty tree would make that tag a lie), then builds the Docker image tagged with the
current commit SHA, loads it into minikube, creates/updates the two Kubernetes Secrets
from `.env` (`scripts/k8s-secrets.sh`), and runs `helm upgrade --install`. Safe to
re-run any time you commit new code.

```
kubectl get pods
```

should show `convoscore-api`, `convoscore-worker`, `convoscore-postgres-0`,
`convoscore-prometheus`, and `convoscore-grafana` all `1/1 Running`.

### Configuration

`NAMESPACE` (default `default`) is honored consistently by `deploy.sh`,
`k8s-secrets.sh`, `teardown.sh`, and all three demo scripts - set it once in your shell
(`export NAMESPACE=convoscore`) to deploy/operate outside the default namespace; add
`-n "$NAMESPACE"` to any `kubectl`/`helm` command below if you do.

The scoring model is set in `charts/convoscore/values.yaml` under `scoring.model`
(default `gpt-4o-mini`), passed through to both the API and worker as the `OPENAI_MODEL`
env var. Override per-deploy with `helm upgrade --install convoscore charts/convoscore
--set image.tag=$(git rev-parse --short HEAD) --set scoring.model=gpt-4o` (or edit
`values.yaml` directly).

## UI access

Everything is `ClusterIP` - reach it via `kubectl port-forward`, one per terminal (or
backgrounded):

| Service | Command | URL |
|---|---|---|
| API + review UI | `kubectl port-forward svc/convoscore-api 8080:8000` | http://localhost:8080/conversations |
| Grafana | `kubectl port-forward svc/convoscore-grafana 3000:3000` | http://localhost:3000 |
| Prometheus | `kubectl port-forward svc/convoscore-prometheus 9090:9090` | http://localhost:9090 |

The review UI lists recent conversations and their status; click one for the full
scored result. `/healthz` and `/readyz` (a real `SELECT 1`, not just a pool checkout)
are plain JSON. `/metrics` is the API's own Prometheus endpoint. Grafana uses anonymous
**viewer** access (a documented local-demo tradeoff, not a production auth setup - see
`DECISIONS.md`) and comes with one dashboard preloaded: processing rate, failures/DLQ
depth, latency p95, OpenAI token usage & cost, and worker/DLQ-collector health (seconds
since last progress - a stale worker looks different from an idle one). A matching
Prometheus alerting rule (`ConvoScoreWorkerPollStale`, `ConvoScoreDlqCollectorStale`,
visible under Prometheus's own `/alerts` - no Alertmanager is deployed) backs the same
signal.

Submit a conversation directly:

```
curl -X POST http://localhost:8080/conversations -H "Content-Type: application/json" \
  -d '{"text":"The customer was frustrated but the issue got resolved quickly."}'
```

## Demos

Reproducible scripts in `scripts/`, run against the real deployed cluster (real OpenAI
calls - no `SCORER_PROVIDER=fake` in Kubernetes). All of them talk to Postgres directly
via `kubectl exec` for authoritative status checks, not the review page's HTML. Requires
the Python environment from step 2.

| Script | Needs | Proves |
|---|---|---|
| `demo-success.sh` | API port-forward (8080) | Both ingestion paths (API and direct S3 upload) reach `completed` with real OpenAI results |
| `demo-failure.sh` | API port-forward (8080) + Prometheus port-forward (9090) | An induced deterministic failure retries 3x, reaches `failed`/`transient_exhausted`, and lands in the DLQ (confirmed by depth delta + a body match, not just "DLQ is nonempty"); healthy conversations keep completing throughout |
| `demo-restart.sh` | none - manages its own port-forward | API/worker/Postgres pods are deleted and recreated (new pod UIDs, `Ready`, correct image tag); the stored result for a conversation submitted before the restart survives **byte-for-byte identical**, including `completed_at` - or the script exits nonzero |

```
PYTHON_BIN=.venv/bin/python bash scripts/demo-success.sh   # Windows: .venv/Scripts/python.exe
```

`demo-failure.sh` submits `"__TRANSIENT_FAIL__ ..."` as the conversation text - a
deterministic trigger honored by both the fake and real scorer, so the failure/DLQ
path doesn't depend on hoping for a real OpenAI error. It takes a few minutes (three
real ~60s SQS visibility-timeout cycles).

## Running the test suite locally

The test suite runs outside Kubernetes, against a local Postgres (not the one in the
cluster), reusing the Python environment from step 2:

```
docker run -d --name convoscore-postgres -p 5432:5432 \
  -e POSTGRES_USER=convoscore -e POSTGRES_PASSWORD=convoscore -e POSTGRES_DB=convoscore \
  postgres:16-alpine

.venv/bin/python -m pytest tests/ -v   # Windows: .venv\Scripts\python
```

Tests use `FakeScorer` (deterministic, no network calls) and never hit the real OpenAI
API.

## Teardown

```
bash scripts/teardown.sh
```

Scoped to ConvoScore's own resources only, safe to run repeatedly, and refuses to run
unless the current kubectl context is `minikube`. In order: the Helm release, the
Postgres PVC (**this permanently deletes stored conversation data**), the two
out-of-band Kubernetes Secrets, `terraform destroy` (needs LocalStack still running -
this is why it runs before the next step), then the LocalStack container itself (**this
also permanently deletes its S3/SQS data** - the PVC deletion above is not the only
irreversible step here).

Not touched by `teardown.sh`, on purpose:

- **minikube itself.** The cluster may be shared with another project. Only stop or
  delete it yourself, and only once you're sure nothing else depends on it:
  `minikube stop` (keeps the cluster, frees resources) or `minikube delete` (removes it
  entirely) - neither is part of the normal ConvoScore teardown path.
- The optional local dev Postgres container from "Running the test suite locally", if
  you started one: `docker rm -f convoscore-postgres` (deletes its data too).
