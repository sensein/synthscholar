-- Migration 004: pgvector embeddings on article_store and review_cache.
-- Run after 001/002/003 to enable semantic search across stored literature
-- and past review results.
--
-- Example:
--     psql "$PRISMA_PG_DSN" -f synthscholar/cache/migrations/004_add_embeddings.sql
--
-- Embedding dimension is 384 (matches sentence-transformers/all-MiniLM-L6-v2,
-- the default backend in synthscholar.embedding). If you swap to a different
-- backend (e.g. OpenAI text-embedding-3-small at 1536 dims) you must drop and
-- recreate these columns with the new VECTOR(N) width.

CREATE EXTENSION IF NOT EXISTS vector;

-- ── article_store: per-article embedding ─────────────────────────────────────

ALTER TABLE article_store
    ADD COLUMN IF NOT EXISTS embedding VECTOR(384);

-- IVF flat index for approximate nearest-neighbour search.
-- `lists = 100` is a reasonable default for ≤ 100k articles; tune up for
-- larger corpora (rule of thumb: rows / 1000).
CREATE INDEX IF NOT EXISTS idx_article_embedding
    ON article_store
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── review_cache: per-review embedding (over question + PICO + criteria) ─────

ALTER TABLE review_cache
    ADD COLUMN IF NOT EXISTS embedding VECTOR(384);

CREATE INDEX IF NOT EXISTS idx_review_embedding
    ON review_cache
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- ── review_cache: tsvector for keyword search across protocol JSON ───────────
-- Indexes the review's research question, PICO terms, and criteria so users
-- can grep historical reviews by topic without paying for an embedding pass.

ALTER TABLE review_cache
    ADD COLUMN IF NOT EXISTS search_vector TSVECTOR
    GENERATED ALWAYS AS (
        setweight(to_tsvector('english',
            coalesce(criteria_json->>'question', '') || ' ' ||
            coalesce(criteria_json->>'title', '')
        ), 'A') ||
        setweight(to_tsvector('english',
            coalesce(criteria_json->>'pico_population',   '') || ' ' ||
            coalesce(criteria_json->>'pico_intervention', '') || ' ' ||
            coalesce(criteria_json->>'pico_comparison',   '') || ' ' ||
            coalesce(criteria_json->>'pico_outcome',      '')
        ), 'B') ||
        setweight(to_tsvector('english',
            coalesce(criteria_json->>'inclusion_criteria', '') || ' ' ||
            coalesce(criteria_json->>'exclusion_criteria', '')
        ), 'C')
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_review_search
    ON review_cache USING GIN (search_vector);
