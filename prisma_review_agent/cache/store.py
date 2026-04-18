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

from .models import CacheEntry, CacheLookupResult, CacheUnavailableError, CacheSchemaError, SimilarityConfig
from .similarity import compute_fingerprint, compute_similarity

logger = logging.getLogger(__name__)


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

    async def connect(self) -> None:
        self._pool = AsyncConnectionPool(
            self._dsn,
            min_size=1,
            max_size=self._pool_size,
            open=False,
        )
        await self._pool.open()
        await self._ensure_schema()

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
                await conn.execute(
                    "SELECT 1 FROM review_cache LIMIT 1"
                )
        except Exception as exc:
            raise CacheSchemaError(
                "review_cache table not found. Run migration: "
                "psql $PRISMA_PG_DSN -f prisma_review_agent/cache/migrations/001_initial.sql"
            ) from exc

    # ── Lookup ────────────────────────────────────────────────────────────────

    async def lookup_exact(self, fingerprint: str) -> CacheEntry | None:
        """Return the cached entry for an exact fingerprint match, or None.

        Returns None if no entry exists or if the entry has expired.
        """
        assert self._pool
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            row = await conn.fetchrow(
                """
                SELECT id, criteria_fingerprint, criteria_json, model_name,
                       result_json, created_at, expires_at
                FROM review_cache
                WHERE criteria_fingerprint = %s
                """,
                fingerprint,
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
    ) -> CacheLookupResult:
        """Scan all live entries for the same model and return the best similarity match.

        Returns a cache hit only if the best score meets or exceeds ``config.threshold``.
        """
        assert self._pool
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await conn.fetch(
                """
                SELECT id, criteria_fingerprint, criteria_json, model_name,
                       result_json, created_at, expires_at
                FROM review_cache
                WHERE model_name = %s
                  AND (expires_at IS NULL OR expires_at > NOW())
                """,
                model_name,
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
    ) -> bool:
        """Persist a completed review result.

        Uses an advisory lock on the fingerprint to prevent duplicate inserts
        under concurrent identical requests.  Returns True if stored, False if
        a race condition detected an existing entry (safe to ignore).
        """
        assert self._pool
        if fingerprint is None:
            from prisma_review_agent.models import ReviewProtocol
            fingerprint = compute_fingerprint(criteria_json, model_name)

        expires_at: datetime | None = None
        if config.ttl_days > 0:
            expires_at = datetime.now(tz=timezone.utc) + timedelta(days=config.ttl_days)

        lock_key = int(fingerprint[:15], 16) & 0x7FFFFFFFFFFFFFFF

        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (lock_key,)
                )
                exists = await conn.fetchval(
                    "SELECT 1 FROM review_cache WHERE criteria_fingerprint = %s",
                    fingerprint,
                )
                if exists:
                    # Update existing entry (force-refresh case)
                    await conn.execute(
                        """
                        UPDATE review_cache
                        SET result_json = %s, created_at = NOW(), expires_at = %s
                        WHERE criteria_fingerprint = %s
                        """,
                        json.dumps(result_json), expires_at, fingerprint,
                    )
                else:
                    await conn.execute(
                        """
                        INSERT INTO review_cache
                            (criteria_fingerprint, criteria_json, model_name, result_json, expires_at)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        fingerprint,
                        json.dumps(criteria_json),
                        model_name,
                        json.dumps(result_json),
                        expires_at,
                    )
        return True

    # ── Admin ─────────────────────────────────────────────────────────────────

    async def get_all_entries(self, include_expired: bool = False) -> list[CacheEntry]:
        assert self._pool
        query = "SELECT * FROM review_cache"
        if not include_expired:
            query += " WHERE expires_at IS NULL OR expires_at > NOW()"
        query += " ORDER BY created_at DESC"
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await conn.fetch(query)
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
    )


def _is_expired(entry: CacheEntry) -> bool:
    if entry.expires_at is None:
        return False
    now = datetime.now(tz=timezone.utc)
    exp = entry.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return now > exp
