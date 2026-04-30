"""PostgreSQL-backed review result cache store."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    _PSYCOPG = True
except ImportError:
    _PSYCOPG = False

from .models import (
    CacheEntry,
    CacheLookupResult,
    CacheUnavailableError,
    CacheSchemaError,
    SimilarityConfig,
    PipelineCheckpoint,
)
from .similarity import compute_fingerprint, compute_similarity

logger = logging.getLogger(__name__)


# ── psycopg3 compatibility helpers ────────────────────────────────────────────
# The cache code was originally written assuming asyncpg's connection-level
# `fetch / fetchrow / fetchval` methods, which don't exist in psycopg3. These
# helpers emulate those signatures using psycopg3's cursor pattern, taking a
# single positional `params` sequence (instead of asyncpg's positional varargs)
# to keep callers tidy.


async def _fetchall_dict(conn, sql, params: tuple = ()):
    """psycopg3 equivalent of asyncpg's ``conn.fetch(sql, *params)``."""
    if not _PSYCOPG:
        raise CacheUnavailableError("psycopg[async] not installed")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def _fetchone_dict(conn, sql, params: tuple = ()):
    """psycopg3 equivalent of asyncpg's ``conn.fetchrow(sql, *params)``."""
    if not _PSYCOPG:
        raise CacheUnavailableError("psycopg[async] not installed")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def _fetchval(conn, sql, params: tuple = ()):
    """psycopg3 equivalent of asyncpg's ``conn.fetchval(sql, *params)``."""
    async with conn.cursor() as cur:
        await cur.execute(sql, params)
        row = await cur.fetchone()
        return row[0] if row else None


class CacheStore:
    """Async PostgreSQL cache store for PRISMA review results.

    Usage::

        async with CacheStore(dsn="postgresql://...") as store:
            result = await store.lookup_exact(fingerprint)
    """

    def __init__(self, dsn: str, pool_size: int = 3) -> None:
        if not _PSYCOPG:
            raise CacheUnavailableError(
                "psycopg[async] not installed. Run: pip install 'psycopg[async]>=3.1' psycopg-pool"
            )
        self._dsn = dsn
        self._pool_size = pool_size
        self._pool: AsyncConnectionPool | None = None
        # Cached at connect-time. True when migration 004 has added the
        # ``embedding`` column on ``review_cache``. Controls whether
        # store_entry writes embeddings and whether semantic-review-search
        # is callable.
        self._has_embeddings: bool = False

    async def connect(self) -> None:
        self._pool = AsyncConnectionPool(
            self._dsn,
            min_size=1,
            max_size=self._pool_size,
            open=False,
        )
        await self._pool.open()
        await self._ensure_schema()
        self._has_embeddings = await self._has_review_embeddings()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def __aenter__(self) -> "CacheStore":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def _ensure_schema(self) -> None:
        assert self._pool
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT 1 FROM review_cache LIMIT 1")
        except Exception as exc:
            raise CacheSchemaError(
                "review_cache table not found. Run migration: "
                "psql $PRISMA_PG_DSN -f synthscholar/cache/migrations/001_initial.sql"
            ) from exc
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT 1 FROM pipeline_checkpoints LIMIT 1")
        except Exception as exc:
            raise CacheSchemaError(
                "pipeline_checkpoints table not found. Run migration: "
                "psql $PRISMA_PG_DSN -f synthscholar/cache/migrations/003_add_pipeline_checkpoints.sql"
            ) from exc

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def lookup_exact(
        self, fingerprint: str, owner_review_id: str = ""
    ) -> CacheEntry | None:
        """Return the cached entry for an exact fingerprint match, or None.

        Only returns entries that are shared (is_shared=TRUE) or owned by
        owner_review_id.  Returns None if no entry exists or entry has expired.
        """
        assert self._pool
        async with self._pool.connection() as conn:
            row = await _fetchone_dict(
                conn,
                """
                SELECT id, criteria_fingerprint, criteria_json, model_name,
                       result_json, created_at, expires_at, review_id, is_shared
                FROM review_cache
                WHERE criteria_fingerprint = %s
                  AND (is_shared = TRUE OR review_id = %s)
                """,
                (fingerprint, owner_review_id),
            )
        if not row:
            return None
        entry = _row_to_entry(row)
        if _is_expired(entry):
            logger.debug("Cache entry %s expired — treating as miss", fingerprint[:12])
            return None
        return entry

    async def lookup_similar(
        self,
        incoming_criteria: dict[str, Any],
        model_name: str,
        config: SimilarityConfig,
        owner_review_id: str = "",
    ) -> CacheLookupResult:
        """Scan all live entries for the same model and return the best similarity match.

        Only considers entries that are shared (is_shared=TRUE) or owned by
        owner_review_id.  Returns a cache hit only if the best score meets or
        exceeds ``config.threshold``.
        """
        assert self._pool
        async with self._pool.connection() as conn:
            rows = await _fetchall_dict(
                conn,
                """
                SELECT id, criteria_fingerprint, criteria_json, model_name,
                       result_json, created_at, expires_at, review_id, is_shared
                FROM review_cache
                WHERE model_name = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
                  AND (is_shared = TRUE OR review_id = %s)
                """,
                (model_name, owner_review_id),
            )

        best_score = 0.0
        best_entry: CacheEntry | None = None

        for row in rows:
            entry = _row_to_entry(row)
            score = compute_similarity(incoming_criteria, entry.criteria_json, config)
            if score > best_score:
                best_score = score
                best_entry = entry

        if best_entry and best_score >= config.threshold:
            best_entry.similarity_score = best_score
            return CacheLookupResult(
                hit=True,
                entry=best_entry,
                similarity_score=best_score,
                matched_fingerprint=best_entry.criteria_fingerprint,
            )
        return CacheLookupResult(hit=False)

    # ── Store ─────────────────────────────────────────────────────────────────

    async def store_entry(
        self,
        criteria_json: dict[str, Any],
        model_name: str,
        result_json: dict[str, Any],
        config: SimilarityConfig,
        fingerprint: str | None = None,
        review_id: str = "",
        is_shared: bool = True,
    ) -> bool:
        """Persist a completed review result.

        Uses an advisory lock on the fingerprint to prevent duplicate inserts
        under concurrent identical requests.  Returns True if stored, False if
        a race condition detected an existing entry (safe to ignore).

        review_id: source review that generated this result (for owner bypass).
        is_shared: when False, only the owner (by review_id) can read this entry.
        """
        assert self._pool
        if fingerprint is None:
            fingerprint = compute_fingerprint(criteria_json, model_name)

        expires_at: datetime | None = None
        if config.ttl_days > 0:
            expires_at = datetime.now(tz=timezone.utc) + timedelta(days=config.ttl_days)

        lock_key = int(fingerprint[:15], 16) & 0x7FFFFFFFFFFFFFFF

        # Generate an embedding from the criteria_json so the row is
        # immediately retrievable by semantic search. Best-effort — if the
        # backend isn't installed we still write the row without it.
        vec_literal: str | None = None
        if self._has_embeddings:
            try:
                from synthscholar.embedding import embed_text
                from synthscholar.cache.article_store import _vector_literal
                # Compose embedding text from the same fields that feed the
                # tsvector — kept consistent with migration 004's GENERATED
                # search_vector definition.
                parts = [
                    str(criteria_json.get("question", "")),
                    str(criteria_json.get("title", "")),
                    str(criteria_json.get("pico_population", "")),
                    str(criteria_json.get("pico_intervention", "")),
                    str(criteria_json.get("pico_comparison", "")),
                    str(criteria_json.get("pico_outcome", "")),
                    str(criteria_json.get("inclusion_criteria", "")),
                    str(criteria_json.get("exclusion_criteria", "")),
                ]
                vec = embed_text(" \n ".join(p for p in parts if p))
                if vec is not None:
                    vec_literal = _vector_literal(vec)
            except Exception as exc:
                logger.info("Review embedding generation skipped: %s", exc)

        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (lock_key,)
                )
                exists = await _fetchval(
                    conn,
                    "SELECT 1 FROM review_cache WHERE criteria_fingerprint = %s",
                    (fingerprint,),
                )
                if exists:
                    if self._has_embeddings:
                        await conn.execute(
                            """
                            UPDATE review_cache
                            SET result_json = %s, created_at = NOW(), expires_at = %s,
                                review_id = %s, is_shared = %s,
                                embedding = COALESCE(%s::vector, embedding)
                            WHERE criteria_fingerprint = %s
                            """,
                            (
                                json.dumps(result_json), expires_at,
                                review_id, is_shared, vec_literal, fingerprint,
                            ),
                        )
                    else:
                        await conn.execute(
                            """
                            UPDATE review_cache
                            SET result_json = %s, created_at = NOW(), expires_at = %s,
                                review_id = %s, is_shared = %s
                            WHERE criteria_fingerprint = %s
                            """,
                            (
                                json.dumps(result_json), expires_at,
                                review_id, is_shared, fingerprint,
                            ),
                        )
                else:
                    if self._has_embeddings:
                        await conn.execute(
                            """
                            INSERT INTO review_cache
                                (criteria_fingerprint, criteria_json, model_name,
                                 result_json, expires_at, review_id, is_shared,
                                 embedding)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector)
                            """,
                            (
                                fingerprint,
                                json.dumps(criteria_json),
                                model_name,
                                json.dumps(result_json),
                                expires_at,
                                review_id,
                                is_shared,
                                vec_literal,
                            ),
                        )
                    else:
                        await conn.execute(
                            """
                            INSERT INTO review_cache
                                (criteria_fingerprint, criteria_json, model_name,
                                 result_json, expires_at, review_id, is_shared)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                fingerprint,
                                json.dumps(criteria_json),
                                model_name,
                                json.dumps(result_json),
                                expires_at,
                                review_id,
                                is_shared,
                            ),
                        )
        return True

    async def set_sharing(self, review_id: str, is_shared: bool) -> int:
        """Update is_shared for all cache entries owned by review_id.

        Call this when share_to_cache is toggled on a review.
        Returns the number of rows updated (0 if no entries exist yet).
        """
        assert self._pool
        async with self._pool.connection() as conn:
            result = await conn.execute(
                "UPDATE review_cache SET is_shared = %s WHERE review_id = %s",
                is_shared,
                review_id,
            )
        return result.pgresult.command_tuples or 0

    # ── Admin ─────────────────────────────────────────────────────────────────

    async def get_all_entries(self, include_expired: bool = False) -> list[CacheEntry]:
        assert self._pool
        query = "SELECT * FROM review_cache"
        if not include_expired:
            query += " WHERE expires_at IS NULL OR expires_at > NOW()"
        query += " ORDER BY created_at DESC"
        async with self._pool.connection() as conn:
            rows = await _fetchall_dict(conn, query)
        return [_row_to_entry(r) for r in rows]

    async def delete_entry(self, fingerprint: str) -> bool:
        assert self._pool
        async with self._pool.connection() as conn:
            result = await conn.execute(
                "DELETE FROM review_cache WHERE criteria_fingerprint = %s", fingerprint
            )
        return result.pgresult.command_tuples == 1

    async def clear_all(self) -> int:
        assert self._pool
        async with self._pool.connection() as conn:
            result = await conn.execute("DELETE FROM review_cache")
        return result.pgresult.command_tuples or 0

    # ── Review search (feature: search past reviews by topic) ────────────────

    async def search_reviews_keyword(
        self, query: str, limit: int = 20, include_expired: bool = False,
    ) -> list[CacheEntry]:
        """Lexical full-text search across cached reviews.

        Searches the ``criteria_json`` columns indexed by migration 004's
        ``search_vector``. Title + research question rank highest, PICO terms
        rank second, inclusion/exclusion criteria rank third.

        Returns a relevance-ordered list of :class:`CacheEntry` objects. Each
        entry's full ``result_json`` is preserved so callers can pluck the
        synthesis text or downstream sections.
        """
        assert self._pool
        sql = """
            SELECT *, ts_rank(search_vector, plainto_tsquery('english', %s)) AS rank
            FROM review_cache
            WHERE search_vector @@ plainto_tsquery('english', %s)
        """
        if not include_expired:
            sql += "  AND (expires_at IS NULL OR expires_at > NOW())\n"
        sql += "ORDER BY rank DESC LIMIT %s"
        async with self._pool.connection() as conn:
            rows = await _fetchall_dict(conn, sql, (query, query, limit))
        return [_row_to_entry(r) for r in rows]

    async def search_reviews_semantic(
        self, query: str, limit: int = 20, include_expired: bool = False,
    ) -> list[CacheEntry]:
        """Semantic (vector-similarity) search across cached reviews.

        Requires migration 004 and the ``[semantic]`` extra. Skips entries
        whose ``embedding`` is NULL (e.g. old reviews ingested before the
        embedding backend was wired). Raises :class:`RuntimeError` with an
        actionable message when prerequisites are missing.
        """
        assert self._pool
        if not self._has_embeddings:
            raise RuntimeError(
                "Semantic review search unavailable — apply migration 004 first: "
                "psql \"$PRISMA_PG_DSN\" -f synthscholar/cache/migrations/004_add_embeddings.sql"
            )
        from synthscholar.embedding import embed_text
        from synthscholar.cache.article_store import _vector_literal
        vec = embed_text(query)
        if vec is None:
            raise RuntimeError(
                "Semantic search backend unavailable — install: "
                "pip install 'synthscholar[semantic]'"
            )
        vec_literal = _vector_literal(vec)
        sql = """
            SELECT *, 1 - (embedding <=> %s::vector) AS similarity
            FROM review_cache
            WHERE embedding IS NOT NULL
        """
        if not include_expired:
            sql += "  AND (expires_at IS NULL OR expires_at > NOW())\n"
        sql += "ORDER BY embedding <=> %s::vector LIMIT %s"
        async with self._pool.connection() as conn:
            rows = await _fetchall_dict(conn, sql, (vec_literal, vec_literal, limit))
        return [_row_to_entry(r) for r in rows]

    async def _has_review_embeddings(self) -> bool:
        assert self._pool
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'review_cache' AND column_name = 'embedding'
                        """
                    )
                    return await cur.fetchone() is not None
        except Exception:
            return False

    # ── Pipeline checkpoint methods (feature 010) ─────────────────────────────

    async def save_checkpoint(self, ckpt: PipelineCheckpoint) -> PipelineCheckpoint:
        """Upsert a pipeline checkpoint by (review_id, stage_name, batch_index).

        Returns the checkpoint with the DB-assigned id and updated_at timestamp.
        """
        assert self._pool
        async with self._pool.connection() as conn:
            row = await _fetchone_dict(
                conn,
                """
                INSERT INTO pipeline_checkpoints
                    (review_id, stage_name, batch_index, status,
                     result_json, error_message, retries, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (review_id, stage_name, batch_index)
                DO UPDATE SET
                    status        = EXCLUDED.status,
                    result_json   = EXCLUDED.result_json,
                    error_message = EXCLUDED.error_message,
                    retries       = EXCLUDED.retries,
                    updated_at    = now()
                RETURNING *
                """,
                (
                    ckpt.review_id,
                    ckpt.stage_name,
                    ckpt.batch_index,
                    ckpt.status,
                    json.dumps(ckpt.result_json),
                    ckpt.error_message,
                    ckpt.retries,
                ),
            )
        return _row_to_checkpoint(row)

    async def load_checkpoint(
        self, review_id: str, stage_name: str, batch_index: int
    ) -> PipelineCheckpoint | None:
        """Load one specific checkpoint, or None if it does not exist."""
        assert self._pool
        async with self._pool.connection() as conn:
            row = await _fetchone_dict(
                conn,
                """
                SELECT * FROM pipeline_checkpoints
                WHERE review_id = %s AND stage_name = %s AND batch_index = %s
                """,
                (review_id, stage_name, batch_index),
            )
        return _row_to_checkpoint(row) if row else None

    async def load_checkpoints(
        self, review_id: str, stage_name: str
    ) -> list[PipelineCheckpoint]:
        """Return all checkpoints for a stage ordered by batch_index."""
        assert self._pool
        async with self._pool.connection() as conn:
            rows = await _fetchall_dict(
                conn,
                """
                SELECT * FROM pipeline_checkpoints
                WHERE review_id = %s AND stage_name = %s
                ORDER BY batch_index
                """,
                (review_id, stage_name),
            )
        return [_row_to_checkpoint(r) for r in rows]

    async def load_completed_stages(self, review_id: str) -> set[str]:
        """Return stage names where every batch is 'complete'.

        Used by the pipeline at startup to skip already-finished stages.
        """
        assert self._pool
        async with self._pool.connection() as conn:
            rows = await _fetchall_dict(
                conn,
                """
                SELECT stage_name,
                       COUNT(*) FILTER (WHERE status != 'complete') AS incomplete
                FROM pipeline_checkpoints
                WHERE review_id = %s
                GROUP BY stage_name
                HAVING COUNT(*) FILTER (WHERE status != 'complete') = 0
                """,
                (review_id,),
            )
        return {r["stage_name"] for r in rows}

    async def mark_stage_complete(self, review_id: str, stage_name: str) -> None:
        """Mark all in_progress batches for a stage as complete."""
        assert self._pool
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                UPDATE pipeline_checkpoints
                SET status = 'complete', updated_at = now()
                WHERE review_id = %s AND stage_name = %s AND status = 'in_progress'
                """,
                review_id, stage_name,
            )

    async def clear_checkpoints(
        self, review_id: str, stage_name: str | None = None
    ) -> None:
        """Delete checkpoints for a review, optionally scoped to one stage."""
        assert self._pool
        async with self._pool.connection() as conn:
            if stage_name is not None:
                await conn.execute(
                    "DELETE FROM pipeline_checkpoints WHERE review_id = %s AND stage_name = %s",
                    review_id, stage_name,
                )
            else:
                await conn.execute(
                    "DELETE FROM pipeline_checkpoints WHERE review_id = %s",
                    review_id,
                )

    # ── Provenance telemetry (migration 005) ─────────────────────────────

    async def store_telemetry(
        self,
        *,
        review_id: str,
        run_configuration: dict | None,
        plan_iterations: list[dict],
        agent_invocations: list[dict],
        search_iterations: list[dict],
    ) -> bool:
        """Persist the full provenance trail for a review run.

        Idempotent: replaces any existing row for this review_id (DELETE+
        INSERT semantics — historic runs are not retained).

        Returns False silently if the review_telemetry table doesn't exist
        (migration 005 not applied), so callers don't need to gate calls
        on a feature flag.
        """
        if not self._pool or not review_id:
            return False
        run_cfg = run_configuration or {}
        model_name = str(run_cfg.get("model_name") or "")
        package_version = str(run_cfg.get("package_version") or "")
        n_inv = len(agent_invocations)
        in_tokens = sum(int(i.get("input_tokens") or 0) for i in agent_invocations)
        out_tokens = sum(int(i.get("output_tokens") or 0) for i in agent_invocations)
        n_plan = max(1, len(plan_iterations))

        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "DELETE FROM review_telemetry WHERE review_id = %s",
                        (review_id,),
                    )
                    await conn.execute(
                        """
                        INSERT INTO review_telemetry (
                            review_id, model_name, package_version,
                            run_configuration, plan_iterations,
                            agent_invocations, search_iterations,
                            n_invocations, total_input_tokens,
                            total_output_tokens, n_plan_iterations
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            review_id, model_name, package_version,
                            json.dumps(run_cfg),
                            json.dumps(plan_iterations),
                            json.dumps(agent_invocations),
                            json.dumps(search_iterations),
                            n_inv, in_tokens, out_tokens, n_plan,
                        ),
                    )
            return True
        except psycopg.errors.UndefinedTable:
            logger.info(
                "review_telemetry table not present (apply migration 005 to enable)"
            )
            return False
        except Exception as exc:
            logger.warning("Failed to store telemetry for %s: %s", review_id, exc)
            return False

    async def load_telemetry(self, review_id: str) -> dict | None:
        """Load the provenance trail for a review, or None if absent."""
        if not self._pool or not review_id:
            return None
        try:
            async with self._pool.connection() as conn:
                row = await _fetchone_dict(
                    conn,
                    "SELECT * FROM review_telemetry WHERE review_id = %s",
                    (review_id,),
                )
        except psycopg.errors.UndefinedTable:
            return None
        except Exception as exc:
            logger.warning("Failed to load telemetry for %s: %s", review_id, exc)
            return None
        return row


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_entry(row: dict[str, Any]) -> CacheEntry:
    return CacheEntry(
        id=row["id"],
        criteria_fingerprint=row["criteria_fingerprint"],
        criteria_json=row["criteria_json"],
        model_name=row["model_name"],
        result_json=row["result_json"],
        created_at=row["created_at"],
        expires_at=row.get("expires_at"),
        review_id=row.get("review_id", ""),
        is_shared=row.get("is_shared", True),
    )


def _is_expired(entry: CacheEntry) -> bool:
    if entry.expires_at is None:
        return False
    now = datetime.now(tz=timezone.utc)
    exp = entry.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return now > exp


def _row_to_checkpoint(row: dict) -> PipelineCheckpoint:
    return PipelineCheckpoint(
        id=row["id"],
        review_id=row["review_id"],
        stage_name=row["stage_name"],
        batch_index=row["batch_index"],
        status=row["status"],
        result_json=row["result_json"] if isinstance(row["result_json"], dict) else {},
        error_message=row.get("error_message", ""),
        retries=row.get("retries", 0),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
