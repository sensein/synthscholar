-- Migration 001: review_cache and article_store tables
-- Run against your PostgreSQL 15+ database before using the cache feature.
-- Example: psql "$PRISMA_PG_DSN" -f prisma_review_agent/cache/migrations/001_initial.sql

-- ── Review result cache ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS review_cache (
    id                   SERIAL PRIMARY KEY,
    criteria_fingerprint VARCHAR(64)  NOT NULL,
    criteria_json        JSONB        NOT NULL,
    model_name           TEXT         NOT NULL,
    result_json          JSONB        NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at           TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cache_fingerprint
    ON review_cache (criteria_fingerprint);

CREATE INDEX IF NOT EXISTS idx_cache_model
    ON review_cache (model_name);

CREATE INDEX IF NOT EXISTS idx_cache_expires
    ON review_cache (expires_at)
    WHERE expires_at IS NOT NULL;

-- ── Article store ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS article_store (
    id             SERIAL PRIMARY KEY,
    pmid           TEXT        NOT NULL,
    title          TEXT        NOT NULL DEFAULT '',
    abstract       TEXT        NOT NULL DEFAULT '',
    authors        TEXT        NOT NULL DEFAULT '',
    journal        TEXT        NOT NULL DEFAULT '',
    year           TEXT        NOT NULL DEFAULT '',
    doi            TEXT        NOT NULL DEFAULT '',
    pmc_id         TEXT        NOT NULL DEFAULT '',
    source         TEXT        NOT NULL DEFAULT '',
    full_text      TEXT        NOT NULL DEFAULT '',
    mesh_terms     JSONB       NOT NULL DEFAULT '[]',
    keywords       JSONB       NOT NULL DEFAULT '[]',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Generated tsvector column for full-text search
    search_vector  TSVECTOR GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(abstract, '')), 'B') ||
        setweight(to_tsvector('english', coalesce(full_text, '')), 'C')
    ) STORED
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_article_pmid
    ON article_store (pmid);

CREATE INDEX IF NOT EXISTS idx_article_doi
    ON article_store (doi)
    WHERE doi != '';

CREATE INDEX IF NOT EXISTS idx_article_pmc
    ON article_store (pmc_id)
    WHERE pmc_id != '';

-- GIN index for fast full-text search
CREATE INDEX IF NOT EXISTS idx_article_search
    ON article_store USING GIN (search_vector);
