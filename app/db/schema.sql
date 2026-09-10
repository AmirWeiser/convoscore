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
    error_category          TEXT CHECK (error_category IN ('validation', 'transient_exhausted')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    processing_started_at   TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_conversations_created_at ON conversations (created_at DESC);
