"""
PRISMA Review Agent — Pydantic AI Systematic Literature Review.

A standalone, agent-based systematic review tool following PRISMA 2020.
"""

from prisma_review_agent.models import (
    Article,
    EvidenceSpan,
    ReviewProtocol,
    PRISMAFlowCounts,
    PRISMAReviewResult,
    RoBTool,
    RoBJudgment,
    GRADECertainty,
    SearchStrategy,
    ScreeningBatchResult,
    RiskOfBiasResult,
    StudyDataExtraction,
    GRADEAssessment,
)
from prisma_review_agent.pipeline import PRISMAReviewPipeline
from prisma_review_agent.export import to_markdown, to_json, to_bibtex

__version__ = "0.2.0"

__all__ = [
    "Article",
    "EvidenceSpan",
    "ReviewProtocol",
    "PRISMAFlowCounts",
    "PRISMAReviewResult",
    "PRISMAReviewPipeline",
    "RoBTool",
    "RoBJudgment",
    "GRADECertainty",
    "SearchStrategy",
    "ScreeningBatchResult",
    "RiskOfBiasResult",
    "StudyDataExtraction",
    "GRADEAssessment",
    "to_markdown",
    "to_json",
    "to_bibtex",
]
