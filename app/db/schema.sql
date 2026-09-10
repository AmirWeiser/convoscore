CREATE TABLE IF NOT EXISTS conversations (
    id                     UUID PRIMARY KEY,
    source_key             TEXT UNIQUE NOT NULL,
    source                 TEXT NOT NULL CHECK (source IN ('api', 's3')),
    status                 TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    input_text             TEXT NOT NULL,
    sentiment              TEXT,
    risk_score              NUMERIC,
    rationale               TEXT,
    result_json             JSONB,
    model                   TEXT,
    prompt_version          TEXT,
    input_tokens            INT,
    output_tokens           INT,
    estimated_cost_usd      NUMERIC,
    error_message           TEXT,
    error_category          TEXT,
    processing_token        UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    processing_started_at   TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ
);

-- Idempotent upgrade path for a database created before processing_token /
-- 'enqueue_failed' existed - CREATE TABLE IF NOT EXISTS alone never alters an
-- already-existing table, so these statements run every time init_schema()
-- does and are no-ops once applied. This one file remains the whole
-- migration strategy for one table (see DECISIONS.md) - both a fresh install
-- and an upgrade of an existing database converge on the same final shape.
ALTER TABLE conversations ADD COLUMN IF NOT EXISTS processing_token UUID;

ALTER TABLE conversations DROP CONSTRAINT IF EXISTS conversations_error_category_check;
ALTER TABLE conversations ADD CONSTRAINT conversations_error_category_check
    CHECK (error_category IN ('validation', 'transient_exhausted', 'enqueue_failed'));

CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations (created_at DESC);
