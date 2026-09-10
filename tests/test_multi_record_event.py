"""Item 4: the worker used to read only Records[0] - a message with more
than one S3 record silently dropped every record after the first, deleting
the message (and losing the remaining records) as soon as the first one
finished. Every record must be processed, and the message deleted only when
every record reached a safe outcome. Requires a running local Postgres (see
README)."""

import json
import uuid
from unittest.mock import MagicMock

from psycopg.rows import dict_row

from app.db import repository
from app.db.pool import init_schema
from app.scoring.fake import FakeScorer
from app import worker as worker_module


def setup_module():
    init_schema()


def _s3_event_record(key: str) -> dict:
    return {"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": key}}}


def _multi_record_message(keys: list[str], receive_count: int = 1) -> dict:
    return {
        "ReceiptHandle": f"handle-{uuid.uuid4()}",
        "Attributes": {"ApproximateReceiveCount": str(receive_count)},
        "Body": json.dumps({"Records": [_s3_event_record(k) for k in keys]}),
    }


def _status(source_key: str) -> str | None:
    with repository.pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            "SELECT status FROM conversations WHERE source_key = %s", (source_key,)
        ).fetchone()
    return row["status"] if row else None


def test_both_records_in_a_two_record_message_are_processed_and_neither_is_dropped(monkeypatch):
    monkeypatch.setattr(worker_module, "get_scorer", lambda: FakeScorer())

    key_a = f"incoming/{uuid.uuid4()}-multi-a.json"
    key_b = f"incoming/{uuid.uuid4()}-multi-b.json"
    s3 = MagicMock()
    bodies = {
        key_a: json.dumps({"text": "first conversation in the batch"}).encode(),
        key_b: json.dumps({"text": "second conversation in the batch"}).encode(),
    }
    s3.get_object.side_effect = lambda Bucket, Key: {"Body": MagicMock(read=lambda: bodies[Key])}
    sqs = MagicMock()

    worker_module._process_message(sqs, s3, "fake-queue-url", _multi_record_message([key_a, key_b]))

    assert _status(key_a) == "completed"
    assert _status(key_b) == "completed"
    sqs.delete_message.assert_called_once()  # both safe - delete once, for the whole message


def test_message_is_not_deleted_if_any_record_is_still_retryable(monkeypatch):
    """One record completes; the other hits a non-exhausted transient
    scoring failure. The message as a whole must stay undeleted - deleting
    it would silently lose the still-retryable record."""
    monkeypatch.setattr(worker_module, "get_scorer", lambda: FakeScorer())

    key_ok = f"incoming/{uuid.uuid4()}-multi-ok.json"
    key_fail = f"incoming/{uuid.uuid4()}-multi-fail.json"
    s3 = MagicMock()
    bodies = {
        key_ok: json.dumps({"text": "this one scores fine"}).encode(),
        key_fail: json.dumps({"text": "__TRANSIENT_FAIL__ this one keeps failing"}).encode(),
    }
    s3.get_object.side_effect = lambda Bucket, Key: {"Body": MagicMock(read=lambda: bodies[Key])}
    sqs = MagicMock()

    # receive_count=1: the failing record has not exhausted retries yet.
    worker_module._process_message(sqs, s3, "fake-queue-url", _multi_record_message([key_ok, key_fail], receive_count=1))

    assert _status(key_ok) == "completed"
    assert _status(key_fail) == "processing"  # claimed, scored, not yet exhausted
    sqs.delete_message.assert_not_called()  # the whole message is kept
