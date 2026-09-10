# ConvoScore — Live Demo Runbook

For the 20–30 minute interview session. Run these in order. Each section has the
exact command, what it should print, how long it takes, and what to do if the live
command doesn't cooperate.

Assumes the system is already deployed (`bash scripts/deploy.sh` already run - see
`README.md`) and `PYTHON_BIN` points at a working interpreter
(`export PYTHON_BIN=.venv/Scripts/python.exe` on Windows, or leave unset elsewhere).

## 0. Before you start (2 min)

```
kubectl get pods
```
Expect: `convoscore-api`, `convoscore-worker`, `convoscore-postgres-0`,
`convoscore-prometheus`, `convoscore-grafana` all `1/1 Running`.

Open three port-forwards, each in its own terminal (or backgrounded):
```
kubectl port-forward svc/convoscore-api 8080:8000
kubectl port-forward svc/convoscore-grafana 3000:3000
kubectl port-forward svc/convoscore-prometheus 9090:9090
```

Have these open in browser tabs, ready but not yet the focus:
- http://localhost:8080/conversations (review UI)
- http://localhost:3000/d/convoscore-overview/convoscore (Grafana)
- http://localhost:9090/alerts (Prometheus)

**Fallback if this section fails:** use the screenshots in this folder
(`review-ui.png`, `grafana-dashboard.png`, `prometheus-alerts.png`) and narrate from
slides 8 and 11 instead of the live pages.

## 1. Success — both ingestion paths (2–3 min)

```
bash scripts/demo-success.sh
```

Expect, within ~15 seconds:
```
=== Direct API ingestion ===
{"id":"...","status":"pending",...}

=== Direct S3 ingestion (no API call) ===
uploaded incoming/demo-s3-....json

=== Waiting for the API-submitted conversation to complete (real OpenAI scoring) ===
  -> reached 'completed' after ...s
API-submitted ...: completed
```

Then switch to the review UI tab, refresh `/conversations` — both new rows appear
`completed`, with real sentiment/risk values. Click one to show the detail page.

**Talking point:** the S3-uploaded one never touched the API at all — same worker,
same result shape, same review page.

**If it fails:** check `kubectl logs deployment/convoscore-worker --tail=20` for the
actual error. Most likely cause is a port-forward that died — re-run the
`kubectl port-forward svc/convoscore-api 8080:8000` command from section 0. Fallback:
show `review-list.png` / the "Persistence and review" slide (8) instead.

## 2. Induced failure → DLQ (4–5 min — this one takes real wall-clock time)

```
bash scripts/demo-failure.sh
```

This submits a healthy conversation and a deterministic-failure conversation
(`__TRANSIENT_FAIL__` trigger — same behavior with the real or fake scorer, so it
doesn't depend on hoping for a genuine OpenAI error), then:

- The healthy one completes in seconds — **point out it's unaffected** while the
  other is actively failing and retrying.
- The failing one retries 3 times, roughly 60 seconds apart (SQS visibility timeout).
  Expect three lines like:
  ```
  {"message": "conversation scoring attempt failed", "receive_count": 1, "exhausted": false, ...}
  {"message": "conversation scoring attempt failed", "receive_count": 2, "exhausted": false, ...}
  {"message": "conversation scoring attempt failed", "receive_count": 3, "exhausted": true, ...}
  ```
- Final state: `failed|transient_exhausted|RuntimeError`, then:
  ```
  confirmed: DLQ depth N (> baseline M) and a message body matches incoming/....json
  ```
- A final post-failure conversation is submitted and completes normally — the worker
  is fully healthy afterward.

**While it's retrying (this is the natural pause point):** switch to Grafana, point
at "Failures / DLQ depth" ticking up, and to Prometheus `/alerts` to show
`ConvoScoreWorkerPollStale` / `ConvoScoreDlqCollectorStale` both `Inactive` — the
worker is busy retrying, not stuck.

**If it fails or looks stuck:** confirm the Prometheus port-forward is up (this
script queries it). Fallback: narrate slide 6 (worker internals) and show
`grafana-dashboard.png`'s "Failures / DLQ depth" panel from an earlier run.

## 3. Restart durability (2–3 min)

```
bash scripts/demo-restart.sh
```

Deletes and recreates the API, worker, and Postgres pods, then diffs a pre-restart
result against the post-restart one. Expect, near the end:
```
=== Comparing the stored result before vs. after the restart ===
before: <id>|negative|0.8|...|2026-...
after:  <id>|negative|0.8|...|2026-...
CONFIRMED: the exact result (including completed_at) survived unchanged - not re-processed.
```

**Talking point:** the check is on `completed_at`, not just `status` — a re-scored
duplicate would also say "completed" but with a different timestamp. This is the
same guarantee claim fencing exists for.

**If it fails:** the script exits nonzero if the snapshots differ — that's a real
finding, not a flaky test, so don't re-run it hoping for a different answer. Check
`kubectl get pods` for anything crash-looping, and fall back to narrating slide 8's
durability bullet plus `git log` showing `test_claim_fencing.py` /
`test_redelivery_no_delete.py` as the underlying proof.

## Recovery playbook (any step)

| Symptom | Fix |
|---|---|
| `curl: (7) Failed to connect` / port-forward dropped | Re-run the relevant `kubectl port-forward` from section 0 |
| A pod is `CrashLoopBackOff` | `kubectl logs <pod>` for the reason; `kubectl delete pod <pod>` lets its controller recreate it |
| Demo script hangs past its expected time | Ctrl-C, check `kubectl get pods` and `kubectl logs deployment/convoscore-worker --tail=30`, then re-run the script (all three are safe to re-run) |
| Need a clean slate mid-demo | `bash scripts/teardown.sh && bash scripts/deploy.sh` (~3–4 minutes total) |

## After the demo

```
bash scripts/teardown.sh
```
Removes only ConvoScore's own resources (Helm release, PVC, Secrets, Terraform-managed
S3/SQS/IAM, the LocalStack container) — never touches minikube itself. See `README.md`
for the full teardown/rebuild story if asked.
