"""
Evidence extraction — delegates to the LLM evidence extraction agent.

No hardcoded heuristics. The agent intelligently identifies relevant
evidence spans, claims, quantitative results, and contradictions
from article text based on the research question.
"""

from __future__ import annotations

from .models import Article, EvidenceSpan
from .agents import AgentDeps, run_evidence_extraction


async def extract_evidence(
    articles: list[Article],
    deps: AgentDeps,
    max_spans: int = 30,
) -> list[EvidenceSpan]:
    """Extract evidence spans from articles using the LLM agent.

    The agent reads each article's abstract and full text, identifies
    relevant evidence for the research question, scores it, and
    labels the claim each span supports.

    Args:
        articles: Included articles to extract evidence from.
        deps: Agent dependencies (protocol, API key, model).
        max_spans: Maximum number of evidence spans to return.

    Returns:
        List of EvidenceSpan objects sorted by relevance.
    """
    spans = await run_evidence_extraction(articles, deps)
    return spans[:max_spans]
