"""Requires a running local Postgres (see README) - not mocked, since the
whole point is proving real row-level locking/timing behavior."""

import time
import uuid

from app.db import repository
from app.db.pool import init_schema


def setup_module():
    init_schema()


def _new_pending(source_key: str) -> uuid.UUID:
    conversation_id = uuid.uuid4()
    repository.create_pending(conversation_id, source_key, "api", "test conversation")
    return conversation_id


def test_fresh_claim_succeeds_once():
    cid = _new_pending(f"incoming/{uuid.uuid4()}.json")
    assert repository.claim_for_processing(cid, visibility_timeout_seconds=60) is True
    # second claim attempt on the same (now 'processing', not stale) row fails
    assert repository.claim_for_processing(cid, visibility_timeout_seconds=60) is False


def test_stale_processing_is_reclaimable():
    cid = _new_pending(f"incoming/{uuid.uuid4()}.json")
    assert repository.claim_for_processing(cid, visibility_timeout_seconds=1) is True
    time.sleep(1.5)
    # visibility_timeout_seconds=1 has elapsed - this simulates a crashed
    # worker; the row must be reclaimable, not stuck forever.
    assert repository.claim_for_processing(cid, visibility_timeout_seconds=1) is True


def test_completed_row_is_never_reclaimed():
    cid = _new_pending(f"incoming/{uuid.uuid4()}.json")
    assert repository.claim_for_processing(cid, visibility_timeout_seconds=0) is True

    class _FakeResult:
        sentiment, risk_score, rationale = "neutral", 0.1, "test"
        model, prompt_version = "fake", "v1"
        input_tokens, output_tokens, estimated_cost_usd = 1, 1, 0.0

    repository.store_result(cid, _FakeResult())
    time.sleep(0.2)
    # even with an expired-looking window, 'completed' is terminal - never reclaimed
    assert repository.claim_for_processing(cid, visibility_timeout_seconds=0) is False


def test_duplicate_source_key_is_idempotent():
    key = f"incoming/{uuid.uuid4()}.json"
    id_a = repository.upsert_pending_for_ingestion(key, "s3", "same text")
    id_b = repository.upsert_pending_for_ingestion(key, "s3", "same text")
    assert id_a == id_b
