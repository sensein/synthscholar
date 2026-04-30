-- 005: Provenance telemetry per review run.
--
-- Stores the full provenance trail (run configuration, plan iterations,
-- agent invocations, search iterations) keyed by review_id. The lighter
-- summary still rides on the review_cache.result_json — this table is
-- the audit-grade source of truth for "how was this analysis produced?".
--
-- One row per review; result is replaced (DELETE+INSERT semantics) when
-- a new run completes for the same review_id. Older runs are NOT kept;
-- to retain history, copy review_id+'/'+timestamp into a new id.

CREATE TABLE IF NOT EXISTS review_telemetry (
    review_id          VARCHAR(128) PRIMARY KEY,
    model_name         TEXT NOT NULL DEFAULT '',
    package_version    TEXT NOT NULL DEFAULT '',
    run_configuration  JSONB NOT NULL DEFAULT '{}'::jsonb,
    plan_iterations    JSONB NOT NULL DEFAULT '[]'::jsonb,
    agent_invocations  JSONB NOT NULL DEFAULT '[]'::jsonb,
    search_iterations  JSONB NOT NULL DEFAULT '[]'::jsonb,
    n_invocations      INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    n_plan_iterations  INTEGER NOT NULL DEFAULT 1,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for "find runs that used this model"
CREATE INDEX IF NOT EXISTS idx_review_telemetry_model
    ON review_telemetry (model_name)
    WHERE model_name != '';

-- Index for "find runs newer than X"
CREATE INDEX IF NOT EXISTS idx_review_telemetry_created
    ON review_telemetry (created_at DESC);
