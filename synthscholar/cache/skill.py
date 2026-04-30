"""pydantic-ai CacheAgent — lookup and store review results via typed tools."""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext

from .models import CacheLookupResult, CacheUnavailableError, SimilarityConfig
from .similarity import compute_fingerprint
from .store import CacheStore

logger = logging.getLogger(__name__)

# The CacheAgent uses a minimal system prompt; all logic is in the tools.
# No model is set at definition — pass model= at agent.run() if using LLM dispatch.
cache_agent: Agent[CacheStore, CacheLookupResult] = Agent(
    deps_type=CacheStore,
    output_type=CacheLookupResult,
    system_prompt=(
        "You are a cache management agent for PRISMA systematic reviews. "
        "Use the lookup_cache tool to check for existing results, and "
        "store_result to persist new ones. Always call exactly one tool per run."
    ),
)


@cache_agent.tool
async def lookup_cache(
    ctx: RunContext[CacheStore],
    criteria_json: dict[str, Any],
    model_name: str,
    config_threshold: float = 0.95,
    config_ttl_days: int = 30,
) -> CacheLookupResult:
    """Check the cache for a matching review result.

    Performs exact-match first (by SHA-256 fingerprint), then fuzzy similarity
    scan if no exact match found.  Returns a CacheLookupResult with hit=True
    and the cached entry if similarity >= config_threshold.
    """
    store = ctx.deps
    config = SimilarityConfig(threshold=config_threshold, ttl_days=config_ttl_days)
    fingerprint = compute_fingerprint(criteria_json, model_name)

    # 1. Exact match
    entry = await store.lookup_exact(fingerprint)
    if entry is not None:
        logger.info("Cache exact hit for fingerprint %s", fingerprint[:12])
        return CacheLookupResult(
            hit=True,
            entry=entry,
            similarity_score=1.0,
            matched_fingerprint=fingerprint,
        )

    # 2. Fuzzy similarity scan
    result = await store.lookup_similar(criteria_json, model_name, config)
    if result.hit:
        logger.info(
            "Cache fuzzy hit (score=%.3f) matched fingerprint %s",
            result.similarity_score, (result.matched_fingerprint or "")[:12],
        )
    return result


@cache_agent.tool
async def store_result(
    ctx: RunContext[CacheStore],
    criteria_json: dict[str, Any],
    model_name: str,
    result_json: dict[str, Any],
    config_threshold: float = 0.95,
    config_ttl_days: int = 30,
) -> bool:
    """Persist a completed review result to the cache.

    Returns True on success, False if a concurrent request already stored
    an entry with the same fingerprint.
    """
    store = ctx.deps
    config = SimilarityConfig(threshold=config_threshold, ttl_days=config_ttl_days)
    fingerprint = compute_fingerprint(criteria_json, model_name)
    stored = await store.store_entry(
        criteria_json=criteria_json,
        model_name=model_name,
        result_json=result_json,
        config=config,
        fingerprint=fingerprint,
    )
    if stored:
        logger.info("Stored review result in cache (fingerprint %s)", fingerprint[:12])
    return stored


# ── Convenience wrappers (bypass LLM dispatch for direct calls) ───────────────

async def cache_lookup(
    store: CacheStore,
    criteria_json: dict[str, Any],
    model_name: str,
    threshold: float = 0.95,
    ttl_days: int = 30,
    owner_review_id: str = "",
) -> CacheLookupResult:
    """Direct cache lookup without LLM dispatch overhead."""
    config = SimilarityConfig(threshold=threshold, ttl_days=ttl_days)
    fingerprint = compute_fingerprint(criteria_json, model_name)
    entry = await store.lookup_exact(fingerprint, owner_review_id=owner_review_id)
    if entry is not None:
        return CacheLookupResult(hit=True, entry=entry, similarity_score=1.0,
                                 matched_fingerprint=fingerprint)
    return await store.lookup_similar(criteria_json, model_name, config,
                                      owner_review_id=owner_review_id)


async def cache_store(
    store: CacheStore,
    criteria_json: dict[str, Any],
    model_name: str,
    result_json: dict[str, Any],
    threshold: float = 0.95,
    ttl_days: int = 30,
    review_id: str = "",
    is_shared: bool = True,
) -> bool:
    """Direct cache store without LLM dispatch overhead."""
    config = SimilarityConfig(threshold=threshold, ttl_days=ttl_days)
    fingerprint = compute_fingerprint(criteria_json, model_name)
    return await store.store_entry(
        criteria_json=criteria_json,
        model_name=model_name,
        result_json=result_json,
        config=config,
        fingerprint=fingerprint,
        review_id=review_id,
        is_shared=is_shared,
    )
