"""
Evidence extraction — delegates to the LLM evidence extraction agent,
then validates every span against its source article (source grounding).

No hardcoded heuristics. The agent intelligently identifies relevant
evidence spans, claims, quantitative results, and contradictions
from article text based on the research question.

All returned spans have passed source grounding validation: the span text
must be traceable to the article's abstract or full text at a minimum
fuzzy-match score. Ungrounded spans are dropped and logged.
"""

from __future__ import annotations

import logging

from .models import Article, EvidenceSpan
from .agents import AgentDeps, run_evidence_extraction
from .validation import filter_grounded, DEFAULT_THRESHOLD

logger = logging.getLogger(__name__)


async def extract_evidence(
    articles: list[Article],
    deps: AgentDeps,
    max_spans: int = 30,
    grounding_threshold: float = DEFAULT_THRESHOLD,
) -> list[EvidenceSpan]:
    """Extract and source-ground evidence spans from articles.

    Runs the LLM extraction agent, then validates every span against the
    text of its cited article. Spans that cannot be matched to source text
    at or above ``grounding_threshold`` are dropped.

    Args:
        articles:             Included articles to extract evidence from.
        deps:                 Agent dependencies (protocol, API key, model).
        max_spans:            Maximum number of grounded spans to return.
        grounding_threshold:  Minimum rapidfuzz score (0–100) to keep a span.

    Returns:
        List of source-grounded EvidenceSpan objects sorted by relevance.
        Each span has ``grounded=True`` and a non-zero ``grounding_score``.
    """
    raw_spans = await run_evidence_extraction(articles, deps)

    verified, report = filter_grounded(raw_spans, articles, threshold=grounding_threshold)

    if report.total > 0 and report.n_grounded < report.total:
        logger.info(
            "Evidence grounding: kept %d/%d spans — %s",
            report.n_grounded, report.total, report.summary(),
        )

    return verified[:max_spans]
