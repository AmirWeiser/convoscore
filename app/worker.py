"""Long-poll worker. Same image as the API, different entrypoint command
(`python -m app.worker`). See DECISIONS.md for the full state-machine design."""

import json
import logging
import signal
import threading
import time
import traceback
from urllib.parse import unquote_plus

from botocore.exceptions import BotoCoreError, ClientError
from prometheus_client import start_http_server

from app import config
from app.aws_clients import s3_client, sqs_client
from app.db import repository
from app.db.pool import init_schema
from app.logging_setup import setup_logging
from app.metrics import (
    conversations_processed_total,
    dlq_depth,
    openai_estimated_cost_usd_total,
    openai_tokens_total,
    processing_duration_seconds,
)
from app.scoring.factory import get_scorer

logger = logging.getLogger("convoscore.worker")

_shutdown = False


def _handle_shutdown_signal(signum, frame):
    global _shutdown
    _shutdown = True


def _safe_message_context(message: dict) -> dict:
    """Best-effort context for logging a failure - never raises, never
    includes conversation content. source_key is included when it can be
    parsed out; conversation_id is not available at this point since it may
    not exist yet (this runs before upsert_pending_for_ingestion)."""
    context = {
        "message_id": message.get("MessageId"),
        "receive_count": message.get("Attributes", {}).get("ApproximateReceiveCount"),
    }
    try:
        body = json.loads(message["Body"])
        records = body.get("Records")
        if records:
            context["source_key"] = records[0]["s3"]["object"]["key"]
    except Exception:
        pass
    return context


def _safe_stack_trace(exc: BaseException) -> str:
    """Stack frames only - file/line/function, always safe - deliberately
    never the exception's own str()/args. A third-party library's exception
    message (OpenAI, boto3, psycopg) is not something this code controls the
    content of, and has been found to be able to echo back request/response
    content in some cases (see the OpenAI refusal-message fix in
    openai_scorer.py) - so no exception message is ever logged or stored
    anywhere in this project, only its type name plus this frame trail. See
    DECISIONS.md, and tests/test_no_sensitive_data_in_logs.py for the test
    that verifies this holds even with synthetic sensitive data forced
    through this exact path."""
    return "".join(traceback.format_tb(exc.__traceback__))


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
    except (BotoCoreError, ClientError) as exc:
        # Transient (network/service issue, not the object's fault) - leave
        # the message for standard SQS redelivery/DLQ. No conversation_id
        # exists yet to record anything against; DLQ depth is the signal.
        # Still logged, on every attempt, not just the last - see
        # DECISIONS.md.
        logger.warning(
            "s3 read failed",
            extra={
                "source_key": key,
                "error_type": type(exc).__name__,
                "receive_count": receive_count,
            },
        )
        return

    try:
        payload = json.loads(raw)
        text = payload["text"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError("'text' must be a non-empty string")
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        # Permanently invalid input - terminal immediately, never retried,
        # never reaches the DLQ. See DECISIONS.md. These particular
        # exception types (JSON position info / a missing key name / this
        # module's own fixed message) don't carry conversation content, so
        # str(exc) is safe here specifically - unlike the scoring path below,
        # which talks to a third-party library and never uses str(exc).
        repository.record_validation_failure(key, "s3", raw, str(exc))
        conversations_processed_total.labels(outcome="failed").inc()
        logger.info(
            "conversation validation failed",
            extra={"source_key": key, "error_category": "validation"},
        )
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
        exhausted = receive_count >= config.MAX_RECEIVE_COUNT
        # Logged on every failed attempt, not only the last one - and never
        # str(exc): a third-party (OpenAI/network) exception's own message is
        # not something this code controls the content of. error_type (the
        # exception's class name) plus a message-stripped stack trace is
        # always safe. See DECISIONS.md and the focused leak test.
        logger.warning(
            "conversation scoring attempt failed",
            extra={
                "conversation_id": str(conversation_id),
                "error_type": type(exc).__name__,
                "receive_count": receive_count,
                "exhausted": exhausted,
                "stack_trace": _safe_stack_trace(exc),
            },
        )
        if exhausted:
            # Last allowed attempt: record why, then leave the message
            # undeleted so SQS's own redrive policy - not us - moves it to
            # the DLQ. DLQ depth is the operational signal; this DB row stays
            # a documented 'failed' record rather than stuck 'processing'.
            # error_message is the exception's type name only - see above.
            repository.mark_failed(conversation_id, "transient_exhausted", type(exc).__name__)
            conversations_processed_total.labels(outcome="failed").inc()
        return  # message stays undeleted either way - standard SQS redelivery

    openai_tokens_total.labels(direction="input").inc(result.input_tokens)
    openai_tokens_total.labels(direction="output").inc(result.output_tokens)
    openai_estimated_cost_usd_total.inc(result.estimated_cost_usd)

    repository.store_result(conversation_id, result)
    processing_duration_seconds.observe(time.monotonic() - start)
    conversations_processed_total.labels(outcome="completed").inc()
    logger.info(
        "conversation completed",
        extra={"conversation_id": str(conversation_id), "model": result.model},
    )
    sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


def _update_dlq_depth_periodically(sqs) -> None:
    try:
        dlq_url = sqs.get_queue_url(QueueName=config.SQS_DLQ_NAME)["QueueUrl"]
    except Exception as exc:
        logger.warning(
            "could not resolve DLQ url for depth gauge",
            extra={"error_type": type(exc).__name__, "stack_trace": _safe_stack_trace(exc)},
        )
        return
    while not _shutdown:
        try:
            attrs = sqs.get_queue_attributes(
                QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
            )
            dlq_depth.set(int(attrs["Attributes"]["ApproximateNumberOfMessages"]))
        except Exception as exc:
            logger.warning(
                "failed to refresh DLQ depth gauge",
                extra={"error_type": type(exc).__name__, "stack_trace": _safe_stack_trace(exc)},
            )
        time.sleep(30)


def main() -> None:
    setup_logging()
    # init_schema() completes fully before the metrics port opens - the
    # startupProbe (Helm chart) now provides the startup grace period
    # instead, so a passing startupProbe genuinely means "has a working DB
    # connection", not just "process is alive". See DECISIONS.md.
    init_schema()
    start_http_server(config.METRICS_PORT)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    sqs = sqs_client()
    s3 = s3_client()
    queue_url = sqs.get_queue_url(QueueName=config.SQS_QUEUE_NAME)["QueueUrl"]

    threading.Thread(target=_update_dlq_depth_periodically, args=(sqs,), daemon=True).start()

    while not _shutdown:
        response = sqs.receive_message(
            QueueUrl=queue_url,
            WaitTimeSeconds=20,
            # One at a time, not five - found that batching let later
            # messages in a batch sit waiting their turn in this loop while
            # an earlier one (bounded, but up to ~40-50s worst case across
            # S3+scorer+DB calls) was still being handled, eating into their
            # own visibility-timeout window under bursty submission. See
            # DECISIONS.md.
            MaxNumberOfMessages=1,
            AttributeNames=["ApproximateReceiveCount"],
        )
        for message in response.get("Messages", []):
            try:
                _process_message(sqs, s3, queue_url, message)
            except Exception as exc:
                # Any unexpected failure (not just the scorer's) must never
                # silently wedge this loop - see DECISIONS.md. Logged with
                # whatever safe context is available - error type + a
                # message-stripped stack trace, never str(exc) - so it's
                # actually debuggable without risking a content/secret leak.
                # Message stays undeleted; standard SQS redelivery/DLQ
                # handles it like any other transient failure.
                logger.error(
                    "unexpected error processing message",
                    extra={
                        **_safe_message_context(message),
                        "error_type": type(exc).__name__,
                        "stack_trace": _safe_stack_trace(exc),
                    },
                )


if __name__ == "__main__":
    main()
