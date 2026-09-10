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
