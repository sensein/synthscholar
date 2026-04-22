-- Migration 003: Add pipeline_checkpoints table for iterative large-review processing
--
-- Stores per-batch results for each pipeline stage, keyed by (review_id, stage_name, batch_index).
-- Enables crash recovery and resumption without reprocessing completed batches.
--
-- Run:  psql "$DATABASE_URL" -f prisma_review_agent/cache/migrations/003_add_pipeline_checkpoints.sql

CREATE TABLE IF NOT EXISTS pipeline_checkpoints (
    id            BIGSERIAL PRIMARY KEY,
    review_id     TEXT        NOT NULL,
    stage_name    TEXT        NOT NULL,
    batch_index   INTEGER     NOT NULL,
    status        TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'in_progress', 'complete', 'failed')),
    result_json   JSONB       NOT NULL DEFAULT '{}',
    error_message TEXT        NOT NULL DEFAULT '',
    retries       INTEGER     NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_pipeline_checkpoint UNIQUE (review_id, stage_name, batch_index)
);

CREATE INDEX IF NOT EXISTS idx_ckpt_review_stage
    ON pipeline_checkpoints (review_id, stage_name);
