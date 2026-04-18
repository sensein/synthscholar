-- Migration 002: add ownership columns to review_cache
-- Run after 001_initial.sql. Safe to run multiple times (IF NOT EXISTS / IF NOT EXISTS).
-- Example: psql "$PRISMA_PG_DSN" -f prisma_review_agent/cache/migrations/002_add_sharing.sql

ALTER TABLE review_cache
    ADD COLUMN IF NOT EXISTS review_id VARCHAR(128) NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS is_shared  BOOLEAN NOT NULL DEFAULT TRUE;

CREATE INDEX IF NOT EXISTS idx_cache_review_id
    ON review_cache (review_id)
    WHERE review_id != '';

CREATE INDEX IF NOT EXISTS idx_cache_is_shared
    ON review_cache (is_shared)
    WHERE is_shared = TRUE;
