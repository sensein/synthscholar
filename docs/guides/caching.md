# PostgreSQL Caching

The caching layer stores completed review results in PostgreSQL and retrieves them
when a new protocol is sufficiently similar — avoiding redundant LLM calls for
near-duplicate research questions.

## Setup

```bash
synthscholar \
  --title "..." \
  --pg-dsn "postgresql://user:pass@localhost/reviews" \
  --auto
```

Migrations are applied automatically on first connection:

| File | Creates |
|------|---------|
| `001_initial.sql` | `review_cache` — result cache keyed by protocol hash |
| `002_add_article_store.sql` | `article_store` — full-text article persistence with GIN tsvector |
| `003_add_pipeline_checkpoints.sql` | `pipeline_checkpoints` — batch-level resumability |

## How Cache Matching Works

1. The incoming protocol is serialised to a canonical string
2. SHA-256 of that string is compared against stored entries for an exact hit
3. If no exact hit, `rapidfuzz` computes fuzzy similarity against all cached protocols
4. If similarity ≥ threshold (default `0.95`), the cached result is returned immediately

```python
pipeline = PRISMAReviewPipeline(
    protocol=protocol,
    api_key="sk-or-...",
    pg_dsn="postgresql://user:pass@localhost/reviews",
    cache_threshold=0.95,   # 0.0–1.0; higher = stricter matching
    cache_ttl_days=30,      # cached results expire after N days
)
```

## Force Refresh

Bypass the cache and recompute even when a matching entry exists:

```bash
synthscholar --pg-dsn "..." --force-refresh --title "..."
```

```python
result = await pipeline.run(force_refresh=True)
```

## Pipeline Checkpoints

For large reviews (many articles), the pipeline writes per-batch checkpoints to
`pipeline_checkpoints`. If a run is interrupted, it resumes from the last checkpoint
rather than restarting from scratch.

Checkpoints are keyed by `(review_id, stage, batch_index)` and store the partial result
as JSON.

## Article Store

Full-text articles retrieved from PubMed and bioRxiv are persisted in `article_store`
with tsvector indexing, enabling fast re-use across reviews that share overlapping
literature.

```python
from synthscholar.cache import ArticleStore

store = ArticleStore(dsn="postgresql://...")
articles = await store.search("sepsis machine learning", limit=50)
```

## Cache Administration

```python
from synthscholar.cache import CacheStore

store = CacheStore(dsn="postgresql://...")

# List cached entries
entries = await store.list_entries()

# Delete a specific entry
await store.delete(entry_id)

# Purge all expired entries
await store.purge_expired()
```
