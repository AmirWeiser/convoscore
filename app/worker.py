"""Long-poll worker. Same image as the API, different entrypoint command
(`python -m app.worker`). See DECISIONS.md for the full state-machine design."""

import json
import signal
import time
from urllib.parse import unquote_plus

from botocore.exceptions import BotoCoreError, ClientError
from prometheus_client import start_http_server

from app import config
from app.aws_clients import s3_client, sqs_client
from app.db import repository
from app.db.pool import init_schema
from app.metrics import conversations_processed_total, processing_duration_seconds
from app.scoring.factory import get_scorer

_shutdown = False


def _handle_shutdown_signal(signum, frame):
    global _shutdown
    _shutdown = True


def _process_message(sqs, s3, queue_url: str, message: dict) -> None:
    receipt_handle = message["ReceiptHandle"]
    receive_count = int(message.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    try:
        body = json.loads(message["Body"])
    except json.JSONDecodeError:
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    records = body.get("Records")
    if not records:
        # S3 sends a one-off TestEvent (no "Records" key) when a bucket
        # notification is first configured - harmless, not a real object
        # event. Drop it.
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    s3_info = records[0]["s3"]
    bucket = s3_info["bucket"]["name"]
    # S3 event keys are URL-encoded (spaces as "+", etc.) - decode before use,
    # or GetObject fails to find any key needing escaping.
    key = unquote_plus(s3_info["object"]["key"])

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        raw = obj["Body"].read().decode("utf-8", errors="replace")
    except (BotoCoreError, ClientError):
        # Transient (network/service issue, not the object's fault) - leave
        # the message for standard SQS redelivery/DLQ. No conversation_id
        # exists yet to record anything against; DLQ depth is the signal.
        return

    try:
        payload = json.loads(raw)
        text = payload["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("'text' must be a non-empty string")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        # Permanently invalid input - terminal immediately, never retried,
        # never reaches the DLQ. See DECISIONS.md.
        repository.record_validation_failure(key, "s3", raw, str(exc))
        conversations_processed_total.labels(outcome="failed").inc()
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    conversation_id = repository.upsert_pending_for_ingestion(key, "s3", text)

    if not repository.claim_for_processing(conversation_id, config.VISIBILITY_TIMEOUT_SECONDS):
        # Already completed/failed, or another in-flight delivery currently
        # owns it - safe no-op.
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
        return

    start = time.monotonic()
    try:
        result = get_scorer().score(text)
    except Exception as exc:
        if receive_count >= config.MAX_RECEIVE_COUNT:
            # Last allowed attempt: record why, then leave the message
            # undeleted so SQS's own redrive policy - not us - moves it to
            # the DLQ. DLQ depth is the operational signal; this DB row stays
            # a documented 'failed' record rather than stuck 'processing'.
            repository.mark_failed(conversation_id, "transient_exhausted", str(exc))
            conversations_processed_total.labels(outcome="failed").inc()
        return  # message stays undeleted either way - standard SQS redelivery

    repository.store_result(conversation_id, result)
    processing_duration_seconds.observe(time.monotonic() - start)
    conversations_processed_total.labels(outcome="completed").inc()
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


def main() -> None:
    # Metrics server opens first, before the blocking DB call - so the
    # liveness TCP check has something to connect to immediately, instead of
    # seeing "connection refused" during a slow Postgres cold start and
    # killing an otherwise-fine, still-starting pod. See DECISIONS.md.
    start_http_server(config.METRICS_PORT)
    init_schema()
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    sqs = sqs_client()
    s3 = s3_client()
    queue_url = sqs.get_queue_url(QueueName=config.SQS_QUEUE_NAME)["QueueUrl"]

    while not _shutdown:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            WaitTimeSeconds=20,
            MaxNumberOfMessages=5,
            AttributeNames=["ApproximateReceiveCount"],
        )
        for message in response.get("Messages", []):
            _process_message(sqs, s3, queue_url, message)


if __name__ == "__main__":
    main()
