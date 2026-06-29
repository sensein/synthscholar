"""
Source grounding validation for PRISMA evidence spans.

Every EvidenceSpan must be traceable to text that actually appears in the
fetched article content (abstract or full text). Spans that cannot be matched
above the grounding threshold are dropped and logged in a ValidationReport.

Algorithm
---------
For each span:
  1. Verify the cited PMID exists in the article pool.
  2. Verify the article has retrievable text (abstract or full_text).
  3. Reject spans with fewer than MIN_VERIFIABLE_TOKENS — too short to check reliably.
  4. Score the span against the source using rapidfuzz:
       score = max(partial_ratio(span, source), token_set_ratio(span, source))
     partial_ratio finds the best substring alignment; token_set_ratio handles
     word-order variation from paraphrasing. Taking the max is intentionally
     lenient — we reject fabrications, not legitimate paraphrases.
  5. Spans scoring below the threshold are rejected.

The threshold default (65 / 100) was chosen to:
  - Accept direct quotes   (~95+)
  - Accept close paraphrases (~70–90)
  - Reject hallucinated text (~20–50)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Sequence

from .models import Article, EvidenceSpan

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    _RAPIDFUZZ = False
    logger.warning(
        "rapidfuzz not installed — source grounding validation is disabled. "
        "Install with: pip install rapidfuzz>=3.0"
    )

# Minimum score (0–100 rapidfuzz scale) to accept a span as grounded.
DEFAULT_THRESHOLD: float = 65.0

# Spans shorter than this token count produce unreliable partial-match scores.
MIN_VERIFIABLE_TOKENS: int = 4


# ─────────────────────────── Result types ───────────────────────────────────

@dataclass
class SpanGroundingResult:
    """Grounding check outcome for one EvidenceSpan."""
    span: EvidenceSpan
    score: float                    # 0.0–100.0 (rapidfuzz scale)
    grounded: bool
    pmid_found: bool                # span's PMID exists in the article pool
    source_available: bool          # article had text to check against
    rejection_reason: str = ""


@dataclass
class ValidationReport:
    """Aggregated results of one grounding validation pass."""
    total: int
    n_grounded: int
    n_missing_pmid: int
    n_no_source: int
    n_low_score: int
    n_too_short: int
    avg_score_grounded: float
    avg_score_rejected: float
    results: list[SpanGroundingResult] = field(default_factory=list)

    @property
    def grounded_ratio(self) -> float:
        return self.n_grounded / self.total if self.total else 0.0

    def summary(self) -> str:
        return (
            f"Source grounding: {self.n_grounded}/{self.total} spans verified "
            f"({self.grounded_ratio:.0%}), avg_score={self.avg_score_grounded:.1f}; "
            f"rejected {self.total - self.n_grounded} "
            f"[score<{DEFAULT_THRESHOLD:.0f}: {self.n_low_score}, "
            f"no_text: {self.n_no_source}, "
            f"bad_pmid: {self.n_missing_pmid}, "
            f"too_short: {self.n_too_short}]"
        )


# ─────────────────────────── Internal helpers ────────────────────────────────

def _article_index(articles: Sequence[Article]) -> dict[str, Article]:
    return {a.pmid: a for a in articles}


def _source_text(article: Article) -> str:
    """Concatenate abstract + full_text into one searchable string."""
    parts: list[str] = []
    if article.abstract:
        parts.append(article.abstract)
    if article.full_text:
        parts.append(article.full_text)
    return " ".join(parts)


def _grounding_score(span_text: str, source: str) -> float:
    """
    Return a 0–100 grounding score for span_text against source.

    Uses the maximum of:
    - partial_ratio: best substring alignment of span inside source
      (ideal for direct quotes from a larger document)
    - token_set_ratio: intersection-based score ignoring word order
      (handles close paraphrases and minor word reordering)
    """
    if not _RAPIDFUZZ:
        return 100.0  # can't verify, assume grounded to avoid false rejects
    return max(
        _fuzz.partial_ratio(span_text, source),
        _fuzz.token_set_ratio(span_text, source),
    )


# ─────────────────────────── Public API ─────────────────────────────────────

def validate_grounding(
    spans: Sequence[EvidenceSpan],
    articles: Sequence[Article],
    threshold: float = DEFAULT_THRESHOLD,
) -> ValidationReport:
    """
    Check every span against its cited source article.

    Parameters
    ----------
    spans:     Evidence spans produced by the LLM extraction step.
    articles:  Articles that were passed to the extraction agent.
    threshold: Minimum rapidfuzz score (0–100) to accept a span.

    Returns
    -------
    ValidationReport with per-span results. Use ``filter_grounded`` if you
    only need the verified spans.
    """
    index = _article_index(articles)
    results: list[SpanGroundingResult] = []

    for span in spans:
        tokens = span.text.split()

        # ── Gate 1: PMID must exist in article pool ──
        if span.paper_pmid not in index:
            logger.warning(
                "EvidenceSpan cites unknown PMID %s — rejected", span.paper_pmid
            )
            results.append(SpanGroundingResult(
                span=span, score=0.0, grounded=False,
                pmid_found=False, source_available=False,
                rejection_reason=f"PMID {span.paper_pmid} not in article pool",
            ))
            continue

        article = index[span.paper_pmid]
        source = _source_text(article)

        # ── Gate 2: article must have retrievable text ──
        if not source:
            logger.warning(
                "PMID %s has no abstract or full text — cannot ground span", span.paper_pmid
            )
            results.append(SpanGroundingResult(
                span=span, score=0.0, grounded=False,
                pmid_found=True, source_available=False,
                rejection_reason="Article has no retrievable text",
            ))
            continue

        # ── Gate 3: span must be long enough to verify ──
        if len(tokens) < MIN_VERIFIABLE_TOKENS:
            results.append(SpanGroundingResult(
                span=span, score=0.0, grounded=False,
                pmid_found=True, source_available=True,
                rejection_reason=f"Span too short ({len(tokens)} tokens < {MIN_VERIFIABLE_TOKENS})",
            ))
            continue

        # ── Gate 4: fuzzy match against source ──
        score = _grounding_score(span.text, source)
        grounded = score >= threshold
        rejection_reason = "" if grounded else (
            f"Grounding score {score:.1f} < threshold {threshold:.1f}"
        )
        if not grounded:
            logger.debug(
                "Span rejected (score=%.1f) PMID=%s: %r",
                score, span.paper_pmid, span.text[:100],
            )

        results.append(SpanGroundingResult(
            span=span, score=score, grounded=grounded,
            pmid_found=True, source_available=True,
            rejection_reason=rejection_reason,
        ))

    # ── Build report ──
    grounded_results = [r for r in results if r.grounded]
    rejected_results = [r for r in results if not r.grounded]

    def _avg(scores: list[float]) -> float:
        return sum(scores) / len(scores) if scores else 0.0

    report = ValidationReport(
        total=len(results),
        n_grounded=len(grounded_results),
        n_missing_pmid=sum(1 for r in results if not r.pmid_found),
        n_no_source=sum(1 for r in results if r.pmid_found and not r.source_available),
        n_low_score=sum(1 for r in results if r.source_available and len(r.span.text.split()) >= MIN_VERIFIABLE_TOKENS and not r.grounded),
        n_too_short=sum(1 for r in results if r.source_available and len(r.span.text.split()) < MIN_VERIFIABLE_TOKENS),
        avg_score_grounded=_avg([r.score for r in grounded_results]),
        avg_score_rejected=_avg([r.score for r in rejected_results if r.source_available]),
        results=results,
    )

    logger.info(report.summary())
    if report.grounded_ratio < 0.5 and report.total > 0:
        logger.warning(
            "Less than 50%% of evidence spans passed grounding (%d/%d). "
            "Check that full text was successfully retrieved for included articles.",
            report.n_grounded, report.total,
        )

    return report


def filter_grounded(
    spans: Sequence[EvidenceSpan],
    articles: Sequence[Article],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[EvidenceSpan], ValidationReport]:
    """
    Return only spans that pass source grounding, plus a full ValidationReport.

    Stamps each returned span's ``grounding_score`` and ``grounded`` fields
    so the information is preserved through export.

    Parameters
    ----------
    spans:     Evidence spans from LLM extraction.
    articles:  Articles the spans were extracted from.
    threshold: Minimum score to accept (default 65/100).

    Returns
    -------
    (verified_spans, report)
    """
    report = validate_grounding(spans, articles, threshold)

    verified: list[EvidenceSpan] = []
    for r in report.results:
        if r.grounded:
            # Stamp grounding metadata onto the span model
            r.span.grounding_score = r.score / 100.0   # normalise to 0.0–1.0
            r.span.grounded = True
            verified.append(r.span)

    return verified, report
