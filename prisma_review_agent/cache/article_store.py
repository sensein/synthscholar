"""PostgreSQL-backed article store — persists fetched articles for source reuse."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

try:
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
    _PSYCOPG = True
except ImportError:
    _PSYCOPG = False

from .models import CacheUnavailableError, StoredArticle

if TYPE_CHECKING:
    from prisma_review_agent.models import Article

logger = logging.getLogger(__name__)


class ArticleStore:
    """Async PostgreSQL store for fetched articles.

    Articles are indexed by PMID (unique) and by a tsvector full-text search
    column so they can be retrieved as source material in future reviews.

    Usage::

        async with ArticleStore(dsn="postgresql://...") as store:
            await store.upsert_articles(articles)
            hits = await store.search_by_keyword("CRISPR sickle cell")
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
            self._dsn, min_size=1, max_size=self._pool_size, open=False
        )
        await self._pool.open()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def __aenter__(self) -> "ArticleStore":
        await self.connect()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── Write ─────────────────────────────────────────────────────────────────

    async def upsert_articles(self, articles: "list[Article]") -> int:
        """Persist a list of articles; updates existing rows on PMID conflict.

        Returns the number of rows inserted or updated.
        """
        assert self._pool
        if not articles:
            return 0

        count = 0
        async with self._pool.connection() as conn:
            async with conn.transaction():
                for a in articles:
                    await conn.execute(
                        """
                        INSERT INTO article_store
                            (pmid, title, abstract, authors, journal, year,
                             doi, pmc_id, source, full_text, mesh_terms, keywords, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, NOW())
                        ON CONFLICT (pmid) DO UPDATE SET
                            title      = EXCLUDED.title,
                            abstract   = EXCLUDED.abstract,
                            authors    = EXCLUDED.authors,
                            journal    = EXCLUDED.journal,
                            year       = EXCLUDED.year,
                            doi        = EXCLUDED.doi,
                            pmc_id     = EXCLUDED.pmc_id,
                            source     = EXCLUDED.source,
                            full_text  = CASE
                                           WHEN EXCLUDED.full_text != ''
                                           THEN EXCLUDED.full_text
                                           ELSE article_store.full_text
                                         END,
                            mesh_terms = EXCLUDED.mesh_terms,
                            keywords   = EXCLUDED.keywords,
                            updated_at = NOW()
                        """,
                        a.pmid, a.title, a.abstract, a.authors, a.journal,
                        a.year, a.doi, a.pmc_id, a.source, a.full_text,
                        json.dumps(a.mesh_terms), json.dumps(a.keywords),
                    )
                    count += 1
        logger.debug("ArticleStore: upserted %d articles", count)
        return count

    # ── Read ──────────────────────────────────────────────────────────────────

    async def get_by_pmids(self, pmids: list[str]) -> "list[Article]":
        """Retrieve articles by PMID list. Returns only those found in the store."""
        if not pmids:
            return []
        assert self._pool
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await conn.fetch(
                "SELECT * FROM article_store WHERE pmid = ANY(%s)", pmids
            )
        return [_row_to_article(r) for r in rows]

    async def search_by_title(self, query: str, limit: int = 20) -> "list[Article]":
        """Full-text search weighted toward title (tsvector rank A)."""
        return await self._fts_search(query, limit, rank_normalization=1)

    async def search_by_keyword(self, query: str, limit: int = 20) -> "list[Article]":
        """Full-text search across title, abstract, and full text."""
        return await self._fts_search(query, limit, rank_normalization=0)

    async def _fts_search(self, query: str, limit: int, rank_normalization: int) -> "list[Article]":
        assert self._pool
        # Convert free-form query to tsquery, falling back to plainto_tsquery
        async with self._pool.connection() as conn:
            conn.row_factory = dict_row
            rows = await conn.fetch(
                """
                SELECT *, ts_rank(search_vector, plainto_tsquery('english', %s), %s) AS rank
                FROM article_store
                WHERE search_vector @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s
                """,
                query, rank_normalization, query, limit,
            )
        return [_row_to_article(r) for r in rows]

    async def count(self) -> int:
        assert self._pool
        async with self._pool.connection() as conn:
            return await conn.fetchval("SELECT COUNT(*) FROM article_store") or 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_article(row: dict[str, Any]) -> "Article":
    from prisma_review_agent.models import Article
    return Article(
        pmid=row["pmid"],
        title=row.get("title", ""),
        abstract=row.get("abstract", ""),
        authors=row.get("authors", ""),
        journal=row.get("journal", ""),
        year=row.get("year", ""),
        doi=row.get("doi", ""),
        pmc_id=row.get("pmc_id", ""),
        source=row.get("source", ""),
        full_text=row.get("full_text", ""),
        mesh_terms=row.get("mesh_terms") or [],
        keywords=row.get("keywords") or [],
    )
