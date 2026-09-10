"""Claim fencing: an older/slower processing attempt must never be able to
overwrite the outcome written by a newer attempt on the same row (a stale
reclaim, or a redelivery that wins a fresh claim), and a duplicate storage
event must never overwrite an already-decided outcome. Requires a running
local Postgres (see README)."""

import uuid

from app.db import repository
from app.db.pool import init_schema


def setup_module():
    init_schema()


class _FakeResult:
    sentiment, risk_score, rationale = "neutral", 0.1, "test"
    model, prompt_version = "fake", "v1"
    input_tokens, output_tokens, estimated_cost_usd = 1, 1, 0.0


def _new_pending(source_key: str) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    repository.create_pending(conversation_id, source_key, "api", "test conversation")
    return conversation_id


def test_malformed_reupload_cannot_change_a_completed_row():
    key = f"incoming/{uuid.uuid4()}.json"
    cid = _new_pending(key)
    token = repository.claim_for_processing(cid, visibility_timeout_seconds=60)
    assert repository.store_result(cid, token, _FakeResult()) is True

    # A duplicate/redelivered storage event for the same source_key, now
    # found malformed on this delivery - must not touch the completed row.
    repository.record_validation_failure(key, "api", "malformed", "ValidationError")

    row = repository.get_claim_conflict_info(cid)
    assert row["status"] == "completed"


def test_stale_claim_is_replaced_and_old_token_cannot_write():
    cid = _new_pending(f"incoming/{uuid.uuid4()}.json")
    old_token = repository.claim_for_processing(cid, visibility_timeout_seconds=0)
    assert old_token is not None

    # visibility_timeout_seconds=0 means immediately stale - a new delivery
    # reclaims the row with a fresh token.
    new_token = repository.claim_for_processing(cid, visibility_timeout_seconds=0)
    assert new_token is not None
    assert new_token != old_token

    # The old (superseded) attempt's token can no longer store a result or
    # mark the row failed - it lost the race.
    assert repository.store_result(cid, old_token, _FakeResult()) is False
    assert repository.mark_failed(cid, old_token, "transient_exhausted", "RuntimeError") is False

    row = repository.get_claim_conflict_info(cid)
    assert row["status"] == "processing"  # untouched by the superseded attempt


def test_current_claim_token_can_still_complete():
    cid = _new_pending(f"incoming/{uuid.uuid4()}.json")
    token = repository.claim_for_processing(cid, visibility_timeout_seconds=60)
    assert repository.store_result(cid, token, _FakeResult()) is True

    row = repository.get_claim_conflict_info(cid)
    assert row["status"] == "completed"


def test_current_claim_token_can_still_mark_failed():
    cid = _new_pending(f"incoming/{uuid.uuid4()}.json")
    token = repository.claim_for_processing(cid, visibility_timeout_seconds=60)
    assert repository.mark_failed(cid, token, "transient_exhausted", "RuntimeError") is True

    row = repository.get_claim_conflict_info(cid)
    assert row["status"] == "failed"
    assert row["error_category"] == "transient_exhausted"


def test_validation_failure_does_not_overwrite_an_already_processing_row():
    # This specific interleaving cannot happen in practice (validation is
    # deterministic per source_key content and always runs before any claim
    # is attempted for that key - see worker.py), but the DB-level guarantee
    # must hold regardless of how it's reached: a validation-failure write
    # must never downgrade an in-flight or terminal row.
    key = f"incoming/{uuid.uuid4()}.json"
    cid = _new_pending(key)
    repository.claim_for_processing(cid, visibility_timeout_seconds=60)

    repository.record_validation_failure(key, "api", "malformed", "ValidationError")

    row = repository.get_claim_conflict_info(cid)
    assert row["status"] == "processing"


def test_existing_completed_validation_and_transient_exhausted_behavior_still_works():
    # completed
    cid1 = _new_pending(f"incoming/{uuid.uuid4()}.json")
    t1 = repository.claim_for_processing(cid1, visibility_timeout_seconds=60)
    assert repository.store_result(cid1, t1, _FakeResult()) is True

    # validation-failed
    key2 = f"incoming/{uuid.uuid4()}.json"
    id2 = repository.record_validation_failure(key2, "s3", "bad", "ValidationError")
    row2 = repository.get_claim_conflict_info(id2)
    assert row2["status"] == "failed" and row2["error_category"] == "validation"

    # transient_exhausted
    cid3 = _new_pending(f"incoming/{uuid.uuid4()}.json")
    t3 = repository.claim_for_processing(cid3, visibility_timeout_seconds=60)
    assert repository.mark_failed(cid3, t3, "transient_exhausted", "RuntimeError") is True
    row3 = repository.get_claim_conflict_info(cid3)
    assert row3["status"] == "failed" and row3["error_category"] == "transient_exhausted"

    # busy redelivery (no claim conflict info needed - claim itself fails)
    cid4 = _new_pending(f"incoming/{uuid.uuid4()}.json")
    repository.claim_for_processing(cid4, visibility_timeout_seconds=60)
    assert repository.claim_for_processing(cid4, visibility_timeout_seconds=60) is None
