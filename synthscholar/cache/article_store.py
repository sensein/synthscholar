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
    from synthscholar.models import Article

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
        # Set at connect-time. True when migration 004 (embedding column) has
        # been applied. Controls whether semantic search is available and
        # whether upsert writes embeddings.
        self._has_embeddings: bool = False

    async def connect(self) -> None:
        self._pool = AsyncConnectionPool(
            self._dsn, min_size=1, max_size=self._pool_size, open=False
        )
        await self._pool.open()
        self._has_embeddings = await self._detect_embedding_column()

    async def _detect_embedding_column(self) -> bool:
        """Check whether migration 004 has been applied."""
        assert self._pool
        try:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = 'article_store'
                          AND column_name = 'embedding'
                        """
                    )
                    return await cur.fetchone() is not None
        except Exception as exc:
            logger.info("ArticleStore embedding-column probe failed: %s", exc)
            return False

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

        When migration 004 has been applied AND the optional ``[semantic]``
        extra is installed, an ``embedding`` is generated per article and
        stored alongside it (in a single batched encode for efficiency).
        Returns the number of rows inserted or updated.
        """
        assert self._pool
        if not articles:
            return 0

        # Optional eager embedding generation. Returns None per article when
        # the backend isn't installed; we then fall back to a non-embedding
        # upsert path.
        embeddings: list = [None] * len(articles)
        if self._has_embeddings:
            try:
                from synthscholar.embedding import embed_batch, article_text_for_embedding
                texts = [article_text_for_embedding(a) for a in articles]
                batch = embed_batch(texts)
                if batch is not None:
                    embeddings = batch
            except Exception as exc:
                logger.info("Embedding generation skipped: %s", exc)

        count = 0
        async with self._pool.connection() as conn:
            async with conn.transaction():
                for a, vec in zip(articles, embeddings):
                    if self._has_embeddings:
                        vec_literal = _vector_literal(vec) if vec is not None else None
                        await conn.execute(
                            """
                            INSERT INTO article_store
                                (pmid, title, abstract, authors, journal, year,
                                 doi, pmc_id, source, full_text, mesh_terms, keywords,
                                 embedding, updated_at)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                    %s::vector, NOW())
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
                                embedding  = COALESCE(EXCLUDED.embedding, article_store.embedding),
                                updated_at = NOW()
                            """,
                            (a.pmid, a.title, a.abstract, a.authors, a.journal,
                             a.year, a.doi, a.pmc_id, a.source, a.full_text,
                             json.dumps(a.mesh_terms), json.dumps(a.keywords),
                             vec_literal),
                        )
                    else:
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
                            (a.pmid, a.title, a.abstract, a.authors, a.journal,
                             a.year, a.doi, a.pmc_id, a.source, a.full_text,
                             json.dumps(a.mesh_terms), json.dumps(a.keywords)),
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
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM article_store WHERE pmid = ANY(%s)",
                    (pmids,),
                )
                rows = await cur.fetchall()
        return [_row_to_article(r) for r in rows]

    async def search_by_title(self, query: str, limit: int = 20) -> "list[Article]":
        """Full-text search weighted toward title (tsvector rank A)."""
        return await self._fts_search(query, limit, rank_normalization=1)

    async def search_by_keyword(self, query: str, limit: int = 20) -> "list[Article]":
        """Full-text search across title, abstract, and full text."""
        return await self._fts_search(query, limit, rank_normalization=0)

    async def _fts_search(self, query: str, limit: int, rank_normalization: int) -> "list[Article]":
        assert self._pool
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT *, ts_rank(search_vector, plainto_tsquery('english', %s), %s) AS rank
                    FROM article_store
                    WHERE search_vector @@ plainto_tsquery('english', %s)
                    ORDER BY rank DESC
                    LIMIT %s
                    """,
                    (query, rank_normalization, query, limit),
                )
                rows = await cur.fetchall()
        return [_row_to_article(r) for r in rows]

    async def search_semantic(self, query: str, limit: int = 20) -> "list[Article]":
        """Semantic (vector-similarity) search across the article corpus.

        Requires migration 004 to be applied (so the ``embedding`` column and
        the IVF flat index exist) and the optional ``[semantic]`` extra to be
        installed (so ``synthscholar.embedding`` can produce a query vector).

        Returns articles ordered by cosine similarity (most similar first),
        skipping rows whose embedding is NULL (e.g. articles ingested before
        migration 004 or before the embedding backend was installed).

        Raises :class:`RuntimeError` with an actionable message when either
        prerequisite is missing.
        """
        assert self._pool
        if not self._has_embeddings:
            raise RuntimeError(
                "Semantic search unavailable — apply migration 004 first: "
                "psql \"$PRISMA_PG_DSN\" -f synthscholar/cache/migrations/004_add_embeddings.sql"
            )
        from synthscholar.embedding import embed_text
        vec = embed_text(query)
        if vec is None:
            raise RuntimeError(
                "Semantic search backend unavailable — install: "
                "pip install 'synthscholar[semantic]'"
            )
        vec_literal = _vector_literal(vec)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    """
                    SELECT *, 1 - (embedding <=> %s::vector) AS similarity
                    FROM article_store
                    WHERE embedding IS NOT NULL
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (vec_literal, vec_literal, limit),
                )
                rows = await cur.fetchall()
        return [_row_to_article(r) for r in rows]

    async def count(self) -> int:
        assert self._pool
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT COUNT(*) FROM article_store")
                row = await cur.fetchone()
                return row[0] if row else 0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row_to_article(row: dict[str, Any]) -> "Article":
    from synthscholar.models import Article
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


def _vector_literal(vec: list[float]) -> str:
    """Render a Python float list as a pgvector ``[v1,v2,...]`` literal.

    Used in lieu of the optional ``pgvector-python`` adapter so we don't
    add a dependency for one feature.
    """
    return "[" + ",".join(repr(float(v)) for v in vec) + "]"
