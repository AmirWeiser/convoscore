import uuid
from typing import Any

from psycopg.rows import dict_row

from app.db.pool import pool


def create_pending(conversation_id: uuid.UUID, source_key: str, source: str, input_text: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO conversations (id, source_key, source, status, input_text)
            VALUES (%s, %s, %s, 'pending', %s)
            """,
            (conversation_id, source_key, source, input_text),
        )


def list_recent(limit: int = 100) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        conn.row_factory = dict_row
        return conn.execute(
            """
            SELECT id, source, status, sentiment, risk_score, created_at
            FROM conversations
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()


def get_by_id(conversation_id: uuid.UUID) -> dict[str, Any] | None:
    with pool.connection() as conn:
        conn.row_factory = dict_row
        return conn.execute(
            "SELECT * FROM conversations WHERE id = %s", (conversation_id,)
        ).fetchone()


def upsert_pending_for_ingestion(source_key: str, source: str, input_text: str) -> uuid.UUID:
    """The idempotency anchor for the storage path (see DECISIONS.md). No-ops
    if a row for this source_key already exists (the API path already
    inserted it); creates a fresh row for a direct-to-S3 upload that never
    touched the API. Either way, returns the row's id."""
    new_id = uuid.uuid4()
    with pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            """
            INSERT INTO conversations (id, source_key, source, status, input_text)
            VALUES (%s, %s, %s, 'pending', %s)
            ON CONFLICT (source_key) DO NOTHING
            RETURNING id
            """,
            (new_id, source_key, source, input_text),
        ).fetchone()
        if row is not None:
            return row["id"]
        existing = conn.execute(
            "SELECT id FROM conversations WHERE source_key = %s", (source_key,)
        ).fetchone()
        return existing["id"]


def record_validation_failure(source_key: str, source: str, input_text: str, error_message: str) -> uuid.UUID:
    """Malformed/invalid S3 object - permanent failure, recorded immediately so
    it's visible in the review UI even though no row existed yet.

    ON CONFLICT DO NOTHING, not DO UPDATE: a redelivered duplicate for a
    source_key that already has an outcome (completed, or already
    failed/validation) must never overwrite it - see DECISIONS.md (claim
    fencing) for why "an older/duplicate attempt can overwrite a newer one"
    is exactly the class of bug this project guards against everywhere, not
    just in the scoring path."""
    new_id = uuid.uuid4()
    with pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            """
            INSERT INTO conversations
                (id, source_key, source, status, input_text, error_category, error_message)
            VALUES (%s, %s, %s, 'failed', %s, 'validation', %s)
            ON CONFLICT (source_key) DO NOTHING
            RETURNING id
            """,
            (new_id, source_key, source, input_text, error_message),
        ).fetchone()
        if row is not None:
            return row["id"]
        existing = conn.execute(
            "SELECT id FROM conversations WHERE source_key = %s", (source_key,)
        ).fetchone()
        return existing["id"]


def record_enqueue_failure(conversation_id: uuid.UUID, error_message: str) -> None:
    """A definite (non-ambiguous) S3 PutObject failure for a row the API
    already inserted as 'pending' - see DECISIONS.md ("ambiguous enqueue").
    Only touches a still-'pending' row: if the object actually did land in
    S3 and the worker already picked it up, this must not clobber real
    progress."""
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET status = 'failed', error_category = 'enqueue_failed', error_message = %s
            WHERE id = %s AND status = 'pending'
            """,
            (error_message, conversation_id),
        )


def claim_for_processing(conversation_id: uuid.UUID, visibility_timeout_seconds: int) -> uuid.UUID | None:
    """Fresh claim, or stale reclaim of a row whose processing_started_at is
    older than the SQS visibility timeout - see DECISIONS.md for why these
    two numbers must match. Generates and stores a fresh claim token
    (processing_token) on every successful claim, and returns it - the
    caller must present this exact token back to store_result()/
    mark_failed() so an older, slower attempt can never overwrite the
    outcome written by a newer stale-reclaim of the same row (claim
    fencing - see DECISIONS.md). Returns None if the claim was not won."""
    token = uuid.uuid4()
    with pool.connection() as conn:
        cur = conn.execute(
            """
            UPDATE conversations
            SET status = 'processing', processing_started_at = now(), processing_token = %s
            WHERE id = %s
              AND (
                status = 'pending'
                OR (status = 'processing'
                    AND processing_started_at < now() - (%s * interval '1 second'))
              )
            """,
            (token, conversation_id, visibility_timeout_seconds),
        )
        return token if cur.rowcount == 1 else None


def get_claim_conflict_info(conversation_id: uuid.UUID) -> dict[str, Any] | None:
    """Used only to explain *why* a claim failed, with enough detail to
    decide whether deleting the message is safe: status alone is not
    enough, since 'failed' covers two very different cases - permanently
    invalid input (a duplicate is safe to delete) and a transient-retry
    exhaustion whose message must be left alone for SQS's own redrive
    policy to move to the DLQ (deleting it here would do that ourselves,
    outside the redrive policy, and skip the DLQ). See DECISIONS.md and
    worker.py."""
    with pool.connection() as conn:
        conn.row_factory = dict_row
        return conn.execute(
            "SELECT status, error_category FROM conversations WHERE id = %s",
            (conversation_id,),
        ).fetchone()


def store_result(conversation_id: uuid.UUID, processing_token: uuid.UUID, result: Any) -> bool:
    """Guarded by (id, status='processing', processing_token) - see
    claim_for_processing(). Returns False, without writing anything, if this
    token is no longer the active claim (a newer stale-reclaim has since
    taken over the row). The caller must treat False as "superseded", not as
    an error, and must re-derive the SQS delete decision from the row's
    current authoritative state rather than assuming success."""
    with pool.connection() as conn:
        cur = conn.execute(
            """
            UPDATE conversations
            SET status = 'completed',
                sentiment = %s, risk_score = %s, rationale = %s,
                model = %s, prompt_version = %s,
                input_tokens = %s, output_tokens = %s, estimated_cost_usd = %s,
                completed_at = now()
            WHERE id = %s AND status = 'processing' AND processing_token = %s
            """,
            (
                result.sentiment, result.risk_score, result.rationale,
                result.model, result.prompt_version,
                result.input_tokens, result.output_tokens, result.estimated_cost_usd,
                conversation_id, processing_token,
            ),
        )
        return cur.rowcount == 1


def mark_failed(conversation_id: uuid.UUID, processing_token: uuid.UUID, error_category: str, error_message: str) -> bool:
    """Guarded the same way as store_result() - see there for what a False
    return means."""
    with pool.connection() as conn:
        cur = conn.execute(
            """
            UPDATE conversations
            SET status = 'failed', error_category = %s, error_message = %s
            WHERE id = %s AND status = 'processing' AND processing_token = %s
            """,
            (error_category, error_message, conversation_id, processing_token),
        )
        return cur.rowcount == 1
