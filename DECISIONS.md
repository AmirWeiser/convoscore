# Decisions

Why ConvoScore is built the way it is. `README.md` covers how to run it.

## Architecture

```
API  ──┐
       ├─► S3 (incoming/{id}.json) ─► SQS ─► Worker ─► OpenAI ─► Postgres ─► review UI
Direct ┘
S3 upload
```

Both ingestion paths converge on the same pipeline. The API never calls OpenAI directly:
it validates the request, inserts a `pending` row, writes the conversation to S3, and
returns `202`. A direct S3 upload (bypassing the API entirely) reaches the same worker
through the same S3 `ObjectCreated` → SQS notification, so there is exactly one scoring
code path regardless of how a conversation entered the system.

Terraform (against LocalStack) provisions the S3 bucket, the processing queue, its DLQ,
the bucket notification, and IAM policies describing the intended least-privilege shape
(LocalStack Community doesn't enforce IAM at the API-call level, so these exist to show
the real-AWS shape, not to actually restrict access here). Helm deploys the app,
Postgres, Prometheus, and Grafana to Kubernetes (minikube). Secrets are created
out-of-band by `scripts/k8s-secrets.sh` and only ever referenced by name from the chart
- never templated into it, never committed.

## State machine

`pending → processing → completed` (terminal), or `→ failed` (terminal) with an
`error_category` of `validation` or `transient_exhausted`.

- **Validation failure**: malformed input (bad JSON, missing/empty `text`). Terminal
  immediately, the SQS message is deleted right away, and it never reaches the DLQ -
  retrying a well-formed rejection of malformed input would never succeed differently.
- **Transient failure**: a scoring attempt raised (network blip, rate limit, the
  deterministic `__TRANSIENT_FAIL__` demo marker). The message is left undeleted for
  standard SQS redelivery. On the attempt where `ApproximateReceiveCount` reaches the
  configured `maxReceiveCount`, the worker marks the row `failed`/`transient_exhausted`
  and *still* leaves the message undeleted - SQS's own redrive policy, not this code,
  is what moves it to the DLQ.
- `completed` and `failed` are both terminal and never reclaimed.

## Idempotency: source_key, stale-reclaim, and the claim/delete fix

`source_key` (the S3 object key) is the idempotency anchor - a `UNIQUE` constraint,
upserted with `ON CONFLICT DO NOTHING` so a direct-S3 upload and an API submission for
the same key never produce two rows, and so a genuine SQS duplicate delivery finds the
same row rather than creating a second one.

A worker claims a row for processing with:

```sql
UPDATE conversations SET status='processing', processing_started_at=now()
WHERE id = %s AND (
  status = 'pending'
  OR (status = 'processing' AND processing_started_at < now() - (:visibility_timeout seconds))
)
```

The staleness threshold is the same number as the SQS queue's `VisibilityTimeout`
(`VISIBILITY_TIMEOUT_SECONDS` / `terraform/variables.tf`'s `visibility_timeout_seconds`)
- one value, two places, kept in sync deliberately: a crashed worker's claim should go
stale at roughly the same time SQS would redeliver the message anyway.

**A real bug was found and fixed in this claim logic** (2026-09-10, commits `421fa5a` and
`fcbbbd4`). The original code treated *any* failed claim as "safe to delete the SQS
message" - but a failed claim has three different causes, and only some of them make the
message a safe-to-drop duplicate:

| Row state when the claim fails | Message action |
|---|---|
| `completed`, or `failed`/`validation` | **Delete** - genuinely done, a duplicate is noise |
| `failed`/`transient_exhausted` | **Leave alone** - already earmarked for SQS's own redrive to the DLQ; deleting it here would remove it from SQS ourselves and skip the DLQ |
| `processing` and **not yet stale** - another delivery is still legitimately working on it | **Leave alone** |

The busy case is the subtle one: SQS's visibility clock starts at message *receipt*, but
`processing_started_at` is only written after the S3 `GetObject` + DB round trip that
follows receipt - the two clocks use the same timeout value but are not synchronized. A
genuine SQS redelivery can arrive in that gap, see a row that's `processing` but not yet
stale by the DB's clock, and - under the old code - have its message deleted
unconditionally. That deletion discarded the *only remaining SQS copy* of a conversation
that hadn't actually finished; if the owning attempt then crashed before completing,
nothing was left to redeliver or reclaim it, and the row was stuck `processing`
permanently. This is what actually caused conversations to vanish during restart-demo
testing - see **Correction: the LocalStack misattribution**, below.

The fix (`repository.get_claim_conflict_info`, `app/worker.py`) looks up `status` *and*
`error_category` before deciding, logs which of the three cases it hit (so a future
disappearance can be traced from the logs alone), and only deletes in the two genuinely
terminal-and-safe cases. `tests/test_redelivery_no_delete.py` reproduces the busy-claim
race deterministically (backdating `processing_started_at` instead of sleeping) and
asserts recovery still works once the original claim genuinely goes stale.

## Correction: the LocalStack misattribution

While investigating conversations vanishing mid-retry during restart-demo testing, an
earlier pass concluded LocalStack's SQS emulation was unreliable under sustained use and
documented it that way. That conclusion was wrong and has been retracted: the actual
cause was the application-level bug described above. Once fixed, the same restart and
DLQ demos were re-run repeatedly against the same LocalStack instance without
recurrence. LocalStack is not a known source of message loss in this project; it was a
suspicion that didn't survive ruling out the real cause.

## Docker image and deployment

One image, two entrypoints: `python -m uvicorn app.main:app` (API) and
`python -m app.worker` (worker) - no `ENTRYPOINT` lock-in, so the Helm chart's two
Deployments just override `command`. Multi-stage `Dockerfile` (builder installs
dependencies into a venv; the runtime stage copies only the venv and `app/`, never git
metadata, tests, or Terraform state), non-root `appuser` (uid 1000), `python:3.14-slim`
to match local dev.

`values.yaml` deliberately has **no default image tag** - a hardcoded tag would need to
be the SHA of a commit containing that exact line, which is self-referential and
unsolvable. `scripts/deploy.sh` always supplies `--set image.tag=$(git rev-parse
--short HEAD)`, and both Deployment templates use Helm's `required` function so an
un-tagged deploy fails loudly instead of silently reusing whatever was last set.

## Observability

- **Metrics** (`prometheus_client`): `conversations_processed_total{outcome}`,
  `processing_duration_seconds`, `openai_tokens_total{direction}`,
  `openai_estimated_cost_usd_total`, `convoscore_dlq_depth` (a worker background thread
  polls the DLQ's `ApproximateNumberOfMessages` every 30s), and API-side
  `http_requests_total{method,path,status}` / `http_request_duration_seconds`. `path` is
  always the route *template* (e.g. `/conversations/{conversation_id}`), never the
  resolved path - a resolved path would put a fresh UUID in a label value on every
  request, exactly the unbounded-cardinality mistake this project avoids elsewhere. This
  requires reading `request.scope["route"]` *after* `call_next()` returns - Starlette's
  router only populates it once request handling reaches the router, which happens
  inside `call_next()` since the metrics middleware sits outside it in the ASGI chain.
  An earlier version read it before `call_next()` and mislabeled every request
  `"unmatched"`; fixed and covered by `tests/test_metrics_route_labels.py`.
- **Logs**: structured JSON (`app/logging_setup.py`), stdout only. Only low-cardinality,
  non-secret fields are ever attached (ids, categories, counts, receive counts). No
  exception's own message or `str(exc)` is ever logged for a third-party call (OpenAI,
  boto3, psycopg) - only `type(exc).__name__` plus a message-stripped stack trace
  (`traceback.format_tb()`, frames only) - because a third-party exception's message can
  echo back request content (an OpenAI refusal's own explanation text was found to do
  exactly this during Phase 9 and was fixed to exclude it). Verified by
  `tests/test_no_sensitive_data_in_logs.py`, which pushes synthetic secret/content
  markers through the real failure path and asserts they appear nowhere in any log
  record or DB column.
- **Prometheus** is a plain Deployment with a static `scrape_configs` ConfigMap,
  deliberately not the Prometheus Operator/ServiceMonitor CRDs - unnecessary complexity
  for one Kubernetes-native scrape target set that never changes shape.
- **Grafana** has one dashboard (processing rate, failure/DLQ depth, latency p95, OpenAI
  token usage & cost) and anonymous admin access - a documented tradeoff for a
  single-viewer local demo, not something to carry into a real deployment (see
  **Limitations**).

## AWS clients and the DB pool

Both `boto3` clients and the Postgres pool use short, explicit timeouts
(`app/aws_clients.py`: 10s connect / 30s read; `app/db/pool.py`: 10s to acquire a pooled
connection) instead of the libraries' own defaults (boto3's default 60s/60s, an
unbounded pool wait) - the worker is a single-threaded poll loop with nothing else to
detect a stall (a bare TCP liveness check on the metrics port stays "up" regardless), so
a slow dependency needs to fail fast and visibly rather than silently wedge it.

`pool.open(wait=True, timeout=30)` is called exactly once at startup, never retried -
`ConnectionPool.open()` is a one-shot lifecycle transition; calling it again after a
failed attempt raises `PoolClosed`, it does not retry the open. If the one call still
fails (e.g. CoreDNS not yet warm on a fresh pod), the process exits and Kubernetes'
`restartPolicy` plus the chart's `startupProbe` grace period is the actual recovery
mechanism - the same self-healing pattern used elsewhere in this design rather than
hand-rolled retry logic.

## Known limitations

- **Grafana anonymous admin access** - fine for a local, single-viewer demo; a real
  deployment would need real auth.
- **OpenAI pricing is a hardcoded estimate** (`app/scoring/cost.py`), not fetched from a
  billing API - documented as an estimate, not a source of truth.
- **No exactly-once processing guarantee** beyond what's described above: the
  claim/delete logic prevents *data loss and premature DLQ bypass*, but a genuine SQS
  duplicate arriving while a row is legitimately busy is deliberately just left alone for
  the owning delivery (or a later stale-reclaim) to resolve - it is not actively
  deduplicated beyond the `source_key` UNIQUE constraint and the claim check.
- **Restart durability is proven at the pod level only** - `scripts/demo-restart.sh`
  deletes and recreates the API/worker/Postgres pods and confirms the stored result
  survives unchanged via the Postgres PVC. It does not prove survival of `minikube
  delete`, a full cluster teardown, or the underlying PVC's backing volume being lost -
  the PVC only survives as long as that volume does.
- **LocalStack is a local AWS emulator, not real AWS** - IAM policies in
  `terraform/main.tf` describe the intended least-privilege shape but are not enforced
  by LocalStack Community edition in this environment.
