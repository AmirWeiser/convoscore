"""Regression test for a real bug: SQS visibility starts at message receipt,
but processing_started_at is only written after the S3/DB round trip in
_process_message - the two clocks are not synchronized even though they use
the same timeout value. A genuine SQS redelivery can therefore arrive while
a row is still legitimately owned by another in-flight delivery and NOT YET
stale by the DB's own clock. The old code deleted the message unconditionally
whenever claim_for_processing() failed, which discarded the only remaining
SQS copy of a conversation that hadn't actually finished - if the owning
attempt then crashed, nothing was left to redeliver or reclaim it. Requires
a running local Postgres (see README)."""

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


def _s3_event_message(key: str, receive_count: int) -> dict:
    return {
        "ReceiptHandle": f"handle-{uuid.uuid4()}",
        "Attributes": {"ApproximateReceiveCount": str(receive_count)},
        "Body": json.dumps(
            {"Records": [{"s3": {"bucket": {"name": "test-bucket"}, "object": {"key": key}}}]}
        ),
    }


def _s3_mock() -> MagicMock:
    s3 = MagicMock()
    s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: json.dumps({"text": "irrelevant test text"}).encode())
    }
    return s3


def _snapshot(conversation_id) -> dict:
    with repository.pool.connection() as conn:
        conn.row_factory = dict_row
        return conn.execute(
            "SELECT * FROM conversations WHERE id = %s", (conversation_id,)
        ).fetchone()


def test_redelivery_while_busy_and_not_stale_does_not_delete_message():
    conversation_id = uuid.uuid4()
    key = f"incoming/{conversation_id}-busy-test.json"
    repository.create_pending(conversation_id, key, "s3", "irrelevant test text")
    # Simulate another in-flight delivery that already won the claim,
    # well within the visibility window (not stale).
    assert repository.claim_for_processing(conversation_id, visibility_timeout_seconds=60) is not None

    sqs = MagicMock()
    worker_module._process_message(sqs, _s3_mock(), "fake-queue-url", _s3_event_message(key, 2))

    sqs.delete_message.assert_not_called()
    assert _snapshot(conversation_id)["status"] == "processing"  # untouched


def test_recovery_after_original_claim_goes_stale(monkeypatch):
    """The message from the test above was preserved, not dropped - once the
    original claim genuinely goes stale, a later delivery must still be able
    to reclaim and complete the conversation. Confirms the fix does not trade
    the vanishing-message bug for a permanently stuck row."""
    monkeypatch.setattr(worker_module, "get_scorer", lambda: FakeScorer())

    conversation_id = uuid.uuid4()
    key = f"incoming/{conversation_id}-recovery-test.json"
    repository.create_pending(conversation_id, key, "s3", "irrelevant test text")
    assert repository.claim_for_processing(conversation_id, visibility_timeout_seconds=60) is not None

    # Simulate the visibility timeout genuinely having elapsed for the
    # original (stuck/crashed) claim, without sleeping in the test.
    with repository.pool.connection() as conn:
        conn.execute(
            "UPDATE conversations SET processing_started_at = now() - interval '120 seconds' WHERE id = %s",
            (conversation_id,),
        )

    sqs = MagicMock()
    worker_module._process_message(sqs, _s3_mock(), "fake-queue-url", _s3_event_message(key, 3))

    sqs.delete_message.assert_called_once()
    assert _snapshot(conversation_id)["status"] == "completed"


def test_duplicate_delivery_for_a_completed_conversation_is_still_deleted():
    """Distinguishing busy-from-terminal must not regress the existing,
    correct cleanup of duplicates for conversations that are genuinely done."""
    conversation_id = uuid.uuid4()
    key = f"incoming/{conversation_id}-completed-test.json"
    repository.create_pending(conversation_id, key, "s3", "irrelevant test text")
    token = repository.claim_for_processing(conversation_id, visibility_timeout_seconds=60)

    class _FakeResult:
        sentiment, risk_score, rationale = "neutral", 0.1, "test"
        model, prompt_version = "fake", "v1"
        input_tokens, output_tokens, estimated_cost_usd = 1, 1, 0.0

    assert repository.store_result(conversation_id, token, _FakeResult()) is True

    sqs = MagicMock()
    worker_module._process_message(sqs, _s3_mock(), "fake-queue-url", _s3_event_message(key, 2))

    sqs.delete_message.assert_called_once()


def test_duplicate_redelivery_of_a_completed_conversation_never_rescoresor_changes_the_result(monkeypatch):
    """The strongest form of the claim-fencing guarantee: not just "a
    duplicate is deleted", but the stored result itself (including
    completed_at) is provably byte-for-byte unchanged, and the scorer is
    never invoked again for an already-completed conversation."""
    conversation_id = uuid.uuid4()
    key = f"incoming/{conversation_id}-no-rescoring-test.json"
    repository.create_pending(conversation_id, key, "s3", "irrelevant test text")
    monkeypatch.setattr(worker_module, "get_scorer", lambda: FakeScorer())

    sqs = MagicMock()
    worker_module._process_message(sqs, _s3_mock(), "fake-queue-url", _s3_event_message(key, 1))
    before = _snapshot(conversation_id)
    assert before["status"] == "completed"

    def _must_not_be_called(_text):
        raise AssertionError("scorer must not be invoked for an already-completed conversation")

    scorer = MagicMock()
    scorer.score.side_effect = _must_not_be_called
    monkeypatch.setattr(worker_module, "get_scorer", lambda: scorer)

    sqs2 = MagicMock()
    worker_module._process_message(sqs2, _s3_mock(), "fake-queue-url", _s3_event_message(key, 2))

    scorer.score.assert_not_called()
    sqs2.delete_message.assert_called_once()
    after = _snapshot(conversation_id)
    assert after == before  # byte-for-byte unchanged, including completed_at


def test_duplicate_delivery_for_a_validation_failure_is_still_deleted():
    """A validation failure is permanently done (never retried, never DLQ'd
    in the first place) - a duplicate for it is safe to delete, same as a
    completed row."""
    conversation_id = uuid.uuid4()
    key = f"incoming/{conversation_id}-validation-test.json"
    repository.create_pending(conversation_id, key, "s3", "irrelevant test text")
    token = repository.claim_for_processing(conversation_id, visibility_timeout_seconds=60)
    repository.mark_failed(conversation_id, token, "validation", "ValueError")

    sqs = MagicMock()
    worker_module._process_message(sqs, _s3_mock(), "fake-queue-url", _s3_event_message(key, 2))

    sqs.delete_message.assert_called_once()


def test_duplicate_delivery_for_transient_exhausted_does_not_delete_or_score(monkeypatch):
    """The whole point of transient_exhausted is that SQS's own redrive
    policy - not this code - moves the message to the DLQ once its own
    maxReceiveCount is reached. A "duplicate" delivery for that row must
    neither delete the message (which would remove it from SQS ourselves
    and skip the DLQ) nor score it again (it's already a terminal outcome)."""

    def _must_not_be_called(_text):
        raise AssertionError("scorer must not be invoked for a transient_exhausted duplicate")

    scorer = MagicMock()
    scorer.score.side_effect = _must_not_be_called
    monkeypatch.setattr(worker_module, "get_scorer", lambda: scorer)

    conversation_id = uuid.uuid4()
    key = f"incoming/{conversation_id}-transient-exhausted-test.json"
    repository.create_pending(conversation_id, key, "s3", "irrelevant test text")
    token = repository.claim_for_processing(conversation_id, visibility_timeout_seconds=60)
    repository.mark_failed(conversation_id, token, "transient_exhausted", "RuntimeError")

    sqs = MagicMock()
    worker_module._process_message(sqs, _s3_mock(), "fake-queue-url", _s3_event_message(key, 2))

    sqs.delete_message.assert_not_called()
    scorer.score.assert_not_called()
    row = _snapshot(conversation_id)
    assert row["status"] == "failed"
    assert row["error_category"] == "transient_exhausted"
