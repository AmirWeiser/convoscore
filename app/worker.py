"""Long-poll worker. Same image as the API, different entrypoint command
(`python -m app.worker`). See DECISIONS.md for the full state-machine design."""

import json
import logging
import signal
import threading
import time
from urllib.parse import unquote_plus

from botocore.exceptions import BotoCoreError, ClientError
from prometheus_client import start_http_server
from pydantic import ValidationError

from app import config
from app.aws_clients import s3_client, sqs_client
from app.db import repository
from app.db.pool import init_schema
from app.logging_setup import setup_logging
from app.metrics import (
    conversations_processed_total,
    dlq_collector_last_success_timestamp_seconds,
    dlq_depth,
    openai_estimated_cost_usd_total,
    openai_tokens_total,
    processing_duration_seconds,
    worker_last_poll_timestamp_seconds,
)
from app.models import ConversationIn
from app.safe_errors import safe_stack_trace
from app.scoring.factory import get_scorer

logger = logging.getLogger("convoscore.worker")

_shutdown = False


def _handle_shutdown_signal(signum, frame):
    global _shutdown
    _shutdown = True


def _safe_message_context(message: dict) -> dict:
    """Best-effort context for logging a failure - never raises, never
    includes conversation content."""
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
    return safe_stack_trace(exc.__traceback__)


def _handle_claim_conflict(conversation_id, key: str, receive_count: int) -> bool:
    """Called whenever this delivery does not hold the active processing
    claim for a row - either because claim_for_processing() itself failed,
    or because a guarded store_result()/mark_failed() reported the claim was
    superseded by a newer attempt after scoring finished. Either way, the
    SQS decision must come from the row's *current* authoritative state, not
    from what this delivery assumed. Returns True iff it is safe to delete
    this delivery's copy of the message (a genuinely terminal outcome);
    False means leave it alone. See DECISIONS.md (claim fencing)."""
    conflict = repository.get_claim_conflict_info(conversation_id)
    status = conflict["status"] if conflict else None
    error_category = conflict["error_category"] if conflict else None

    if status == "completed" or (status == "failed" and error_category != "transient_exhausted"):
        logger.info(
            "duplicate/superseded delivery for a terminal conversation - safe to delete",
            extra={
                "conversation_id": str(conversation_id),
                "source_key": key,
                "status": status,
                "error_category": error_category,
                "receive_count": receive_count,
            },
        )
        return True

    if status == "failed" and error_category == "transient_exhausted":
        logger.info(
            "duplicate/superseded delivery for a transient_exhausted conversation - "
            "leaving message for SQS's own redrive to the DLQ",
            extra={
                "conversation_id": str(conversation_id),
                "source_key": key,
                "status": status,
                "error_category": error_category,
                "receive_count": receive_count,
            },
        )
        return False

    # 'processing' (not yet stale), or a same-moment 'pending' race - another
    # delivery owns or is about to own this claim. Leave the message
    # undeleted: standard SQS redelivery/the stale-reclaim window is what
    # resolves this, not this delivery deleting it. This is the exact bug
    # fixed in commit 421fa5a - never regress it.
    logger.info(
        "claim skipped or superseded - conversation still owned by another in-flight "
        "delivery, leaving message for redelivery",
        extra={
            "conversation_id": str(conversation_id),
            "source_key": key,
            "status": status,
            "receive_count": receive_count,
        },
    )
    return False


def _process_record(sqs, s3, s3_info: dict, receive_count: int) -> bool:
    """Handles exactly one S3 record from an event message's Records list.
    Returns True iff this record reached an outcome safe to acknowledge
    (terminal and correctly recorded); False means the whole message must
    stay undeleted, since one SQS message can carry multiple records and a
    message can only be deleted or kept as a whole - see DECISIONS.md and
    _process_message()."""
    bucket = s3_info["bucket"]["name"]
    # S3 event keys are URL-encoded (spaces as "+", etc.) - decode before use,
    # or GetObject fails to find any key needing escaping.
    key = unquote_plus(s3_info["object"]["key"])

    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        raw = obj["Body"].read().decode("utf-8", errors="replace")
    except (BotoCoreError, ClientError) as exc:
        # Transient (network/service issue, not the object's fault) - leave
        # the message for standard SQS redelivery/DLQ. Logged on every
        # attempt, not just the last - see DECISIONS.md.
        logger.warning(
            "s3 read failed",
            extra={
                "source_key": key,
                "error_type": type(exc).__name__,
                "receive_count": receive_count,
            },
        )
        return False

    try:
        payload = json.loads(raw)
        parsed = ConversationIn.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        # Permanently invalid input - terminal immediately, never retried,
        # never reaches the DLQ. Both ingestion paths share this one
        # validation contract (ConversationIn) - see DECISIONS.md. JSON
        # decode errors carry only position info, safe to store verbatim;
        # Pydantic's ValidationError can embed the offending input value
        # (input_value=...) in its formatted message, which could be
        # conversation content, so only its type name is ever stored/logged
        # for that case - never str(exc).
        error_message = str(exc) if isinstance(exc, json.JSONDecodeError) else type(exc).__name__
        repository.record_validation_failure(key, "s3", raw, error_message)
        conversations_processed_total.labels(outcome="failed").inc()
        logger.info(
            "conversation validation failed",
            extra={"source_key": key, "error_category": "validation"},
        )
        return True
    text = parsed.text

    conversation_id = repository.upsert_pending_for_ingestion(key, "s3", text)

    token = repository.claim_for_processing(conversation_id, config.VISIBILITY_TIMEOUT_SECONDS)
    if token is None:
        return _handle_claim_conflict(conversation_id, key, receive_count)

    start = time.monotonic()
    try:
        result = get_scorer().score(text)
    except Exception as exc:
        exhausted = receive_count >= config.MAX_RECEIVE_COUNT
        # Logged on every failed attempt, not only the last one - and never
        # str(exc): a third-party (OpenAI/network) exception's own message is
        # not something this code controls the content of. See DECISIONS.md.
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
        if not exhausted:
            return False  # message stays undeleted - standard SQS redelivery

        marked = repository.mark_failed(conversation_id, token, "transient_exhausted", type(exc).__name__)
        if marked:
            conversations_processed_total.labels(outcome="failed").inc()
            return False  # leave undeleted - SQS's own redrive moves it to the DLQ
        # Superseded: a newer claim has since taken over this row (e.g. a
        # stale reclaim). Our outcome must not overwrite theirs - defer to
        # the row's current authoritative state instead of assuming.
        return _handle_claim_conflict(conversation_id, key, receive_count)

    openai_tokens_total.labels(direction="input").inc(result.input_tokens)
    openai_tokens_total.labels(direction="output").inc(result.output_tokens)
    openai_estimated_cost_usd_total.inc(result.estimated_cost_usd)

    marked = repository.store_result(conversation_id, token, result)
    processing_duration_seconds.observe(time.monotonic() - start)
    if marked:
        conversations_processed_total.labels(outcome="completed").inc()
        logger.info(
            "conversation completed",
            extra={"conversation_id": str(conversation_id), "model": result.model},
        )
        return True
    # Superseded: a newer claim (stale reclaim) has since taken over this row
    # - do NOT delete the message purely because *our* scoring finished. The
    # DB row is authoritative; defer to the same conflict handling used
    # anywhere else a claim is contested.
    logger.info(
        "claim superseded - result computed but not stored (a newer attempt owns this row)",
        extra={"conversation_id": str(conversation_id), "source_key": key, "receive_count": receive_count},
    )
    return _handle_claim_conflict(conversation_id, key, receive_count)


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

    # A single SQS message can carry more than one S3 record. Every record
    # is processed through the same per-record path, and the message is
    # deleted only if *every* record reached a safe-to-acknowledge outcome -
    # deleting on a partial result would silently drop whichever record
    # wasn't actually done yet. See DECISIONS.md.
    all_safe = True
    for record in records:
        safe = _process_record(sqs, s3, record["s3"], receive_count)
        all_safe = all_safe and safe

    if all_safe:
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)


def _update_dlq_depth_periodically(sqs) -> None:
    dlq_url = None
    backoff = 5
    while not _shutdown and dlq_url is None:
        try:
            dlq_url = sqs.get_queue_url(QueueName=config.SQS_DLQ_NAME)["QueueUrl"]
        except Exception as exc:
            # Bounded exponential backoff, not a permanent give-up - a
            # transient LocalStack/AWS hiccup at worker startup must not
            # silently disable DLQ-depth observability for the rest of the
            # process's life. See DECISIONS.md.
            logger.warning(
                "could not resolve DLQ url for depth gauge - retrying",
                extra={"error_type": type(exc).__name__, "stack_trace": _safe_stack_trace(exc), "retry_in_seconds": backoff},
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
    while not _shutdown:
        try:
            attrs = sqs.get_queue_attributes(
                QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"]
            )
            dlq_depth.set(int(attrs["Attributes"]["ApproximateNumberOfMessages"]))
            dlq_collector_last_success_timestamp_seconds.set(time.time())
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
                # silently wedge this loop - see DECISIONS.md. Never str(exc).
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
        # Set after every poll cycle, including empty ones - a TCP liveness
        # check on the metrics port only proves the process is alive, not
        # that this loop is making progress. See DECISIONS.md.
        worker_last_poll_timestamp_seconds.set(time.time())


if __name__ == "__main__":
    main()
