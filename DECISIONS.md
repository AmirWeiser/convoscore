# Decisions

Why ConvoScore is built the way it is. `README.md` covers how to run it.

## Architecture

```
API  ──┐
       ├─► S3 (incoming/{id}.json) ─► SQS ─► Worker ─► OpenAI ─► Postgres ─► review UI
Direct ┘
S3 upload
```

**Both ingestion paths converge through S3 and SQS** so there is exactly one scoring
code path regardless of how a conversation entered the system - the worker cannot tell
(and does not need to) whether a given S3 object was written by the API or uploaded
directly. The API never calls OpenAI itself: it validates the request, inserts a
`pending` row, writes the conversation to S3, and returns `202`.

**Processing is asynchronous, and SQS exists alongside S3** because S3 alone only
offers "an object was written," not a durable, retryable, at-least-once work queue with
visibility timeouts and a dead-letter destination. Scoring calls an external LLM with
non-trivial, variable latency; doing that synchronously inside the API request would
tie request latency to OpenAI's, and a slow/failing OpenAI call would have no clean
retry story. SQS's S3 event notification gives a queue "for free" on top of the S3
write the API already needs to make (for direct uploads to work at all).

**PostgreSQL** was chosen over a NoSQL store because the domain is a single, simple,
strongly-typed record type queried by a handful of fixed shapes (recent list, by id,
claim-by-status) - relational + `CHECK` constraints for the state machine's legality is
a better fit than a schemaless store bought for scale this project doesn't have.
Pod-restart durability is proven by `scripts/demo-restart.sh`: it captures a completed
row's full result (including `completed_at`) before deleting the Postgres pod, and
after the StatefulSet recreates it against the **same PVC**, asserts the row is
byte-for-byte identical - not just "status is completed again," which a re-scored
duplicate would also show.

## State machine

`pending → processing → completed` (terminal), or `→ failed` (terminal) with an
`error_category` of `validation`, `transient_exhausted`, or `enqueue_failed`.

- **Validation failure**: malformed input (bad JSON, missing/empty/non-string/
  whitespace-only/over-length `text`) - both ingestion paths validate through the same
  `ConversationIn` Pydantic model. Terminal immediately, the SQS message is deleted
  right away, never reaches the DLQ.
- **Transient failure**: a scoring attempt raised. The message is left undeleted for
  standard SQS redelivery. On the attempt where `ApproximateReceiveCount` reaches
  `maxReceiveCount`, the worker marks the row `failed`/`transient_exhausted` and *still*
  leaves the message undeleted - SQS's own redrive policy, not this code, moves it to
  the DLQ.
- **Enqueue failure**: the API's `PutObject` raised *and* a follow-up `HeadObject`
  definitively confirmed the object was never stored (see "Ambiguous enqueue" below).

## Idempotency, at-least-once delivery, and claim fencing

SQS is at-least-once, never exactly-once, and an OpenAI call cannot be made atomic with
the database commit that records its result - two real consequences follow directly
from that, and neither is eliminated, only bounded:

- **A duplicate message for an already-decided row must not re-decide it.**
  `source_key` (the S3 object key) is the idempotency anchor - `UNIQUE`, upserted with
  `ON CONFLICT DO NOTHING` - so a duplicate delivery finds the same row instead of
  creating a second one, and a duplicate validation-failure write can never downgrade an
  already-completed or already-processing row.
- **Claim fencing prevents an older attempt from overwriting a newer one.** A worker
  claims a row with a fresh `processing_token` (`UPDATE ... WHERE status='pending' OR
  (status='processing' AND processing_started_at < now() - visibility_timeout)`, the
  same staleness threshold as the SQS queue's own `VisibilityTimeout` - one number, two
  places, by design). `store_result`/`mark_failed` are guarded by `WHERE id=%s AND
  status='processing' AND processing_token=%s`: if a stale-reclaim has since taken the
  row, the older attempt's guarded write affects zero rows ("superseded") and must not
  be treated as success - it re-reads the row's current authoritative state instead of
  assuming one, and never deletes the only remaining SQS copy of a row that hasn't
  actually finished (that exact bug - "a failed claim always means safe to delete" -
  shipped once and was fixed; the regression test for it must never go red again).
- **The remaining window: a duplicate paid OpenAI call.** If a worker crashes *after*
  OpenAI returns a result but *before* the guarded `store_result` commits, the row is
  still `processing`, will eventually go stale, and a later delivery will call OpenAI
  again. This is inherent to "SQS is at-least-once and an OpenAI call can't be atomic
  with a DB commit" - not a bug, and not fully eliminated by anything in this project.
  A transactional outbox or idempotency-keyed LLM call would close it; out of scope for
  this take-home (see "Deliberately left out").
- **A single SQS message can carry more than one S3 record.** Every record in a message
  is processed independently through the same per-record path; the message is deleted
  only when *every* record reached a safe, terminal outcome - a partial delete would
  silently lose whichever record wasn't actually done.

### Ambiguous enqueue

The API's `PutObject` can raise for reasons that don't prove S3 never stored the
object (a timeout can mean S3 accepted it and emitted a notification while the client
never saw the response). An unconditional compensating delete on any exception would
risk erasing a conversation the worker is about to process. Instead: on a `PutObject`
exception, a bounded `HeadObject` check decides - object exists → treat as accepted
(202); a definitive not-found → record a visible `enqueue_failed` row (never a hard
delete) and return `502`; the check itself inconclusive → leave the row visibly
`pending` and return a retryable `503`. A production system would replace this with a
transactional outbox or a dedicated reconciler; that's deliberately not built here (see
"Deliberately left out").

## Timeouts and retries

- **boto3 clients**: 10s connect / 30s read (not tighter - SQS long-polling uses
  `WaitTimeSeconds=20`, and the read timeout must stay comfortably above that).
- **DB pool**: 10s to acquire a pooled connection; `pool.open(wait=True, timeout=30)`
  called exactly once at startup, never retried (`ConnectionPool.open()` is a one-shot
  lifecycle transition - retrying it raises `PoolClosed`, it does not retry the open).
  If that one call fails, the process exits and Kubernetes' `restartPolicy` plus the
  chart's generous `startupProbe` grace period is the actual recovery mechanism.
- **OpenAI**: `OPENAI_TIMEOUT_SECONDS` (20s default) per call, `tenacity`-driven retries
  (`OPENAI_MAX_RETRIES`, default 3) with random exponential backoff; the SDK's own
  built-in retries are disabled (`max_retries=0`) so there's exactly one retry
  mechanism, not two nested ones.
- **SQS redelivery**: governed by `VisibilityTimeoutSeconds` (60s) and `maxReceiveCount`
  (3), matched by `VISIBILITY_TIMEOUT_SECONDS`/`MAX_RECEIVE_COUNT` in the app - the same
  numbers in Terraform and in the app config, not independently chosen.

## Secrets and least privilege

Kubernetes Secrets (`convoscore-openai`, `convoscore-postgres`) are created out-of-band
by `scripts/k8s-secrets.sh` - never templated into the Helm chart, never committed.
`POSTGRES_PASSWORD` has no fixed fallback: if unset, a strong random value is generated
once and persisted only in the gitignored `.env`, and the connection string
percent-encodes it so URI-reserved characters can't produce a malformed
`DATABASE_URL`. The API never receives `OPENAI_API_KEY` at all (it never calls OpenAI).
Terraform's IAM policies describe the intended least-privilege shape per component -
the API can `PutObject` only under `incoming/*`; the worker can `GetObject` under
`incoming/*`, `ReceiveMessage`/`DeleteMessage`/`GetQueueAttributes` only on the
processing queue's own ARN, and `GetQueueUrl`/`GetQueueAttributes` only on the DLQ's own
ARN (not the processing queue's broader grant, and no wildcard resources) - but
**LocalStack Community does not enforce IAM at the API-call level**, so these exist to
show the real-AWS shape, not to actually restrict access in this environment.

## Observability

- **Metrics** (`prometheus_client`): conversion outcomes, processing/HTTP latency,
  OpenAI token usage and estimated cost (a hardcoded per-model pricing table - an
  estimate, not a billing source of truth), DLQ depth, and two health gauges that make
  the worker honest: `convoscore_worker_last_poll_timestamp_seconds` (set after every
  poll cycle, including empty ones - a bare TCP liveness check on the metrics port only
  proves the process is alive, not that the loop is making progress) and
  `convoscore_dlq_collector_last_success_timestamp_seconds` (so a stopped DLQ collector
  can't silently look identical to a genuinely empty DLQ). Kubernetes liveness still
  checks the process/metrics socket; Prometheus is what actually detects a wedged loop.
- **Logs**: structured JSON, stdout only, low-cardinality/non-secret fields exclusively.
  No exception's own message or `str(exc)` is ever logged for a third-party call or
  through the global formatter - only the type name plus stack frames. An OpenAI
  refusal's own explanation text was found to be able to echo back flagged input and is
  excluded on the same principle.
- **What an on-call engineer would watch**: `convoscore_dlq_depth` rising (something is
  failing repeatedly), the worker/DLQ-collector staleness gauges (the loop or the
  collector has stopped, not just "quiet"), `http_requests_total{status="5xx"}` rate,
  and `openai_estimated_cost_usd_total`'s rate of change (a runaway cost signal).

## Deliberately left out (right-sizing this take-home)

CI, a service mesh, Argo CD, Vault, a message-broker replacement for SQS, a new web
framework, an ORM, autoscaling, a transactional outbox/reconciler for the ambiguous-
enqueue window, and a broad test suite beyond focused regression coverage for the
behaviors above. Prometheus is a plain static-scrape Deployment, not the Prometheus
Operator - unnecessary complexity for one fixed scrape target set. No Alertmanager -
the two alerting rules are visible in Prometheus's own `/alerts` and on the Grafana
dashboard, nothing routes or pages on them.

## What would change in production

Managed database and queue/storage services (RDS/ElastiCache, real SQS/S3) instead of
a self-hosted Postgres pod and LocalStack; workload identity (IRSA or equivalent)
instead of static `test`/`test` AWS credentials; an external secret manager instead of
manually-created Kubernetes Secrets; authenticated Grafana with real users/roles instead
of anonymous viewer access; high availability (multiple API/worker replicas, a real HA
Postgres topology) instead of one replica each; a real migration tool instead of one
idempotent `schema.sql`; autoscaling (HPA on the worker, tied to queue depth); alert
routing to an on-call system (Alertmanager/PagerDuty) instead of Prometheus-only
visibility; persistent Prometheus/Grafana storage (`emptyDir` today - metrics and
dashboards do not survive pod recreation); backups (Postgres snapshots, S3 versioning);
TLS/Ingress instead of plain `kubectl port-forward`; retention/privacy controls for
conversation content; and stronger reconciliation semantics for the ambiguous-enqueue
window (a transactional outbox, or an idempotency-keyed OpenAI call) to close the
remaining duplicate-call gap described above.

## Remaining truths, stated plainly

- A crash after OpenAI returns but before the guarded database commit can cause another
  paid OpenAI call for the same conversation (see "claim fencing" above).
- Prometheus and Grafana data use `emptyDir` and do not survive pod recreation.
- LocalStack Community does not enforce the IAM policies Terraform defines.
- Pod-restart durability is demonstrated (`scripts/demo-restart.sh`); full cluster
  deletion or LocalStack-volume-loss durability is not, and isn't claimed to be.
- Cost values are estimates from a hardcoded pricing table, not a billing API.
- This project does not claim exactly-once processing anywhere, and none of the
  guarantees above should be read as one.
