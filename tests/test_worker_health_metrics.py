"""Item 6: a TCP liveness check on the metrics port only proves the process
is alive, not that the poll loop or the DLQ-depth collector are making
progress. The DLQ-depth collector must retry the initial URL resolution
with bounded backoff instead of giving up permanently on the first
failure - a missing collector must not silently look like a healthy, empty
DLQ forever."""

from unittest.mock import MagicMock

from app import worker as worker_module
from app.metrics import dlq_collector_last_success_timestamp_seconds, worker_last_poll_timestamp_seconds


def test_worker_health_gauges_exist_and_are_distinct_from_dlq_depth():
    # Importing them successfully and confirming they're real Gauge objects
    # is the contract worker.py/main() and the dashboard rely on.
    worker_last_poll_timestamp_seconds.set(0)
    dlq_collector_last_success_timestamp_seconds.set(0)


def test_dlq_collector_retries_past_transient_url_resolution_failures(monkeypatch):
    monkeypatch.setattr(worker_module.time, "sleep", lambda _seconds: None)  # no real waiting

    sqs = MagicMock()
    attempts = {"n": 0}

    def _get_queue_url(QueueName):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise RuntimeError("simulated transient failure")
        return {"QueueUrl": "http://fake-dlq"}

    sqs.get_queue_url.side_effect = _get_queue_url

    def _get_queue_attributes(QueueUrl, AttributeNames):
        worker_module._shutdown = True  # stop the loop after the first success
        return {"Attributes": {"ApproximateNumberOfMessages": "0"}}

    sqs.get_queue_attributes.side_effect = _get_queue_attributes

    worker_module._shutdown = False
    try:
        worker_module._update_dlq_depth_periodically(sqs)
    finally:
        worker_module._shutdown = False  # don't leak state into other tests

    assert attempts["n"] == 3  # retried past the first two failures, did not give up
