"""Proves app/db/schema.sql is a safe, idempotent migration, not just a
fresh-install script. CREATE TABLE IF NOT EXISTS alone never alters an
already-existing table, so a database created before processing_token /
'enqueue_failed' existed must still be upgraded by init_schema() - both a
fresh install and an upgrade of an existing database must converge on the
same final shape. Requires a running local Postgres (see README)."""

import uuid

from psycopg.rows import dict_row

from app.db.pool import init_schema, pool

_OLD_SCHEMA = """
    CREATE TABLE conversations (
        id UUID PRIMARY KEY,
        source_key TEXT UNIQUE NOT NULL,
        source TEXT NOT NULL CHECK (source IN ('api', 's3')),
        status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
        input_text TEXT NOT NULL,
        sentiment TEXT,
        risk_score NUMERIC,
        rationale TEXT,
        result_json JSONB,
        model TEXT,
        prompt_version TEXT,
        input_tokens INT,
        output_tokens INT,
        estimated_cost_usd NUMERIC,
        error_message TEXT,
        error_category TEXT CHECK (error_category IN ('validation', 'transient_exhausted')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        processing_started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ
    )
"""


def setup_module():
    init_schema()  # just to open the pool - each test drops/recreates the table itself


def _columns() -> set[str]:
    with pool.connection() as conn:
        conn.row_factory = dict_row
        return {
            row["column_name"]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'conversations'"
            ).fetchall()
        }


def test_fresh_install_creates_final_shape():
    with pool.connection() as conn:
        conn.execute("DROP TABLE IF EXISTS conversations")

    init_schema()

    assert "processing_token" in _columns()
    with pool.connection() as conn:
        # The new error_category value must be accepted on a fresh install.
        conn.execute(
            "INSERT INTO conversations (id, source_key, source, status, input_text, error_category) "
            "VALUES (%s, %s, 'api', 'failed', 'x', 'enqueue_failed')",
            (uuid.uuid4(), f"incoming/fresh-install-{uuid.uuid4()}.json"),
        )


def test_upgrade_from_pre_fencing_schema_adds_column_and_widens_constraint():
    with pool.connection() as conn:
        conn.execute("DROP TABLE IF EXISTS conversations")
        conn.execute(_OLD_SCHEMA)

    assert "processing_token" not in _columns()  # old shape, before migrating

    init_schema()

    assert "processing_token" in _columns()
    with pool.connection() as conn:
        # The pre-migration CHECK constraint rejected 'enqueue_failed' -
        # init_schema() must have widened it, not left the old one in place.
        conn.execute(
            "INSERT INTO conversations (id, source_key, source, status, input_text, error_category) "
            "VALUES (%s, %s, 'api', 'failed', 'x', 'enqueue_failed')",
            (uuid.uuid4(), f"incoming/upgrade-{uuid.uuid4()}.json"),
        )
    # Re-running the migration must be a true no-op - it must never touch
    # existing rows.
    with pool.connection() as conn:
        old_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO conversations (id, source_key, source, status, input_text) "
            "VALUES (%s, %s, 'api', 'pending', 'x')",
            (old_id, f"incoming/pre-existing-{old_id}.json"),
        )
    init_schema()
    with pool.connection() as conn:
        conn.row_factory = dict_row
        row = conn.execute(
            "SELECT status FROM conversations WHERE id = %s", (old_id,)
        ).fetchone()
    assert row is not None and row["status"] == "pending"
