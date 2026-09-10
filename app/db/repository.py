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


def delete(conversation_id: uuid.UUID) -> None:
    """Used for the compensating delete when the S3 write fails after the DB
    insert (wired in Phase 4) - kept here since it's a repository-level operation."""
    with pool.connection() as conn:
        conn.execute("DELETE FROM conversations WHERE id = %s", (conversation_id,))


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
    it's visible in the review UI even though no row existed yet."""
    new_id = uuid.uuid4()
    with pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            """
            INSERT INTO conversations
                (id, source_key, source, status, input_text, error_category, error_message)
            VALUES (%s, %s, %s, 'failed', %s, 'validation', %s)
            ON CONFLICT (source_key) DO UPDATE
                SET status = 'failed', error_category = 'validation', error_message = EXCLUDED.error_message
            RETURNING id
            """,
            (new_id, source_key, source, input_text, error_message),
        ).fetchone()
        return row["id"]


def claim_for_processing(conversation_id: uuid.UUID, visibility_timeout_seconds: int) -> bool:
    """Fresh claim, or stale reclaim of a row whose processing_started_at is
    older than the SQS visibility timeout - see DECISIONS.md for why these
    two numbers must match. Returns True iff this call won the claim."""
    with pool.connection() as conn:
        cur = conn.execute(
            """
            UPDATE conversations
            SET status = 'processing', processing_started_at = now()
            WHERE id = %s
              AND (
                status = 'pending'
                OR (status = 'processing'
                    AND processing_started_at < now() - (%s * interval '1 second'))
              )
            """,
            (conversation_id, visibility_timeout_seconds),
        )
        return cur.rowcount == 1


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


def store_result(conversation_id: uuid.UUID, result: Any) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET status = 'completed',
                sentiment = %s, risk_score = %s, rationale = %s,
                model = %s, prompt_version = %s,
                input_tokens = %s, output_tokens = %s, estimated_cost_usd = %s,
                completed_at = now()
            WHERE id = %s
            """,
            (
                result.sentiment, result.risk_score, result.rationale,
                result.model, result.prompt_version,
                result.input_tokens, result.output_tokens, result.estimated_cost_usd,
                conversation_id,
            ),
        )


def mark_failed(conversation_id: uuid.UUID, error_category: str, error_message: str) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE conversations
            SET status = 'failed', error_category = %s, error_message = %s
            WHERE id = %s
            """,
            (error_category, error_message, conversation_id),
        )
