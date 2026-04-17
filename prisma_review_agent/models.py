"""
Pydantic models for PRISMA 2020 Systematic Review.

All data models use Pydantic v2 for validation, serialization, and
structured output from pydantic-ai agents.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field


# ────────────────────────── Enums ──────────────────────────────────────

class ScreeningStage(str, Enum):
    TITLE_ABSTRACT = "title_abstract"
    FULL_TEXT = "full_text"


class ScreeningDecisionType(str, Enum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class InclusionStatus(str, Enum):
    PENDING = "pending"
    INCLUDED = "included"
    EXCLUDED = "excluded"


class RoBJudgment(str, Enum):
    LOW = "Low"
    SOME_CONCERNS = "Some concerns"
    HIGH = "High"
    UNCLEAR = "Unclear"


class GRADECertainty(str, Enum):
    HIGH = "High"
    MODERATE = "Moderate"
    LOW = "Low"
    VERY_LOW = "Very Low"
    NOT_ASSESSED = "Not assessed"


class RoBTool(str, Enum):
    """Risk of bias assessment tools for different study designs."""
    # Randomized trials
    ROB_2 = "RoB 2"
    JADAD = "Jadad Scale"
    # Non-randomized / observational
    ROBINS_I = "ROBINS-I"
    ROBINS_E = "ROBINS-E"
    NOS = "Newcastle-Ottawa Scale"
    # Diagnostic accuracy
    QUADAS_2 = "QUADAS-2"
    # Qualitative studies
    CASP = "CASP Qualitative Checklist"
    # Prevalence / cross-sectional
    JBI = "JBI Critical Appraisal"
    # Case reports / case series
    MURAD = "Murad Tool"
    # Animal studies
    SYRCLE = "SYRCLE"
    # Mixed / general
    MINORS = "MINORS"
    ROBIS = "ROBIS"


class EvidenceStrength(str, Enum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


# ────────────────────────── Core Models ────────────────────────────────

class Article(BaseModel):
    """A research article from PubMed or bioRxiv."""
    pmid: str
    title: str = ""
    abstract: str = ""
    authors: str = ""
    journal: str = ""
    year: str = ""
    doi: str = ""
    pmc_id: str = ""
    mesh_terms: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    source: str = ""
    full_text: str = ""
    hop_level: int = 0
    parent_id: str = ""
    inclusion_status: InclusionStatus = InclusionStatus.PENDING
    exclusion_reason: str = ""
    risk_of_bias: Optional[RiskOfBiasResult] = None
    extracted_data: Optional[StudyDataExtraction] = None
    quality_score: float = 0.0

    @property
    def citation(self) -> str:
        doi_link = f" https://doi.org/{self.doi}" if self.doi else ""
        return f"{self.authors} ({self.year}). {self.title}. *{self.journal}*.{doi_link}"

    @property
    def short_author(self) -> str:
        if not self.authors:
            return "Unknown"
        first = self.authors.split(",")[0].strip()
        return f"{first} et al." if "," in self.authors else first

    def to_context_block(self, index: int) -> str:
        """Format article for LLM context."""
        ft = f"\nFull-text excerpt: {self.full_text[:800]}" if self.full_text else ""
        rob = ""
        if self.risk_of_bias:
            rob = f"\nRoB: {self.risk_of_bias.overall}"
        data = ""
        if self.extracted_data and self.extracted_data.key_findings:
            data = f"\nFindings: {'; '.join(self.extracted_data.key_findings[:3])}"
        return (
            f"--- [{index}] PMID:{self.pmid} | {self.source} ---\n"
            f"Title: {self.title}\nAuthors: {self.authors}\n"
            f"Journal: {self.journal} ({self.year})\nDOI: {self.doi}\n"
            f"Abstract: {(self.abstract or 'N/A')[:800]}\n"
            f"MeSH: {', '.join(self.mesh_terms[:8])}\n"
            f"Keywords: {', '.join(self.keywords[:8])}"
            f"{rob}{data}{ft}"
        )


class EvidenceSpan(BaseModel):
    """An extracted evidence sentence from an article."""
    text: str
    paper_pmid: str
    paper_title: str = ""
    section: str = ""
    relevance_score: float = 0.0
    claim: str = ""
    doi: str = ""
    grounding_score: float = 0.0   # 0.0–1.0; set by validation.filter_grounded
    grounded: bool = False          # True only after passing source grounding check


class ExtractedEvidenceItem(BaseModel):
    """A single evidence span extracted by the LLM from article text."""
    quote: str = Field(description="Exact or close paraphrase from the article")
    claim: str = Field(default="", description="What this evidence supports or refutes")
    section: str = Field(default="", description="Where in the article: abstract, methods, results, discussion")
    relevance: float = Field(default=0.5, ge=0.0, le=1.0, description="Relevance to the research question")
    is_quantitative: bool = Field(default=False, description="Contains effect sizes, p-values, CIs, etc.")


class ArticleEvidenceExtraction(BaseModel):
    """LLM-extracted evidence spans from a single article."""
    pmid: str
    evidence: list[ExtractedEvidenceItem] = Field(default_factory=list)
    overall_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    summary: str = Field(default="", description="One-sentence summary of what this article contributes")


class BatchEvidenceExtraction(BaseModel):
    """Evidence extracted from a batch of articles."""
    articles: list[ArticleEvidenceExtraction] = Field(default_factory=list)


# ────────────────── LLM Structured Output Models ──────────────────────

class SearchStrategy(BaseModel):
    """LLM-generated search strategy."""
    pubmed_queries: list[str] = Field(default_factory=list)
    biorxiv_queries: list[str] = Field(default_factory=list)
    mesh_terms: list[str] = Field(default_factory=list)
    key_concepts: list[str] = Field(default_factory=list)
    rationale: str = ""


class ScreeningDecision(BaseModel):
    """Screening decision for a single article."""
    index: int
    decision: ScreeningDecisionType
    reason: str = ""
    relevance_score: float = Field(default=0.5, ge=0.0, le=1.0)


class ScreeningBatchResult(BaseModel):
    """Batch screening result from LLM."""
    decisions: list[ScreeningDecision]


class RoBDomainAssessment(BaseModel):
    """Risk of bias assessment for a single domain."""
    domain: str
    judgment: RoBJudgment
    support: str = ""


class RiskOfBiasResult(BaseModel):
    """Per-study risk of bias assessment."""
    assessments: list[RoBDomainAssessment] = Field(default_factory=list)
    overall: RoBJudgment = RoBJudgment.UNCLEAR
    summary: str = ""


class ExtractedItem(BaseModel):
    """A single extracted data item with confidence."""
    value: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class StudyDataExtraction(BaseModel):
    """Structured data extracted from a study."""
    study_design: str = "Unknown"
    sample_size: str = ""
    population: str = ""
    intervention: str = ""
    comparator: str = ""
    outcomes: list[str] = Field(default_factory=list)
    key_findings: list[str] = Field(default_factory=list)
    effect_measures: list[str] = Field(default_factory=list)
    follow_up: str = ""
    funding: str = ""
    extracted: dict[str, ExtractedItem] = Field(default_factory=dict)


class GRADEDomainRating(BaseModel):
    """GRADE domain rating."""
    rating: str = "No downgrade"
    explanation: str = ""


class GRADEAssessment(BaseModel):
    """GRADE certainty of evidence assessment."""
    outcome: str = ""
    domains: dict[str, GRADEDomainRating] = Field(default_factory=dict)
    overall_certainty: GRADECertainty = GRADECertainty.NOT_ASSESSED
    summary: str = ""


class ClaimEvidence(BaseModel):
    """A single claim with its supporting evidence."""
    claim: str
    pmids: list[str] = Field(default_factory=list)
    evidence_text: str = ""
    strength: EvidenceStrength = EvidenceStrength.MODERATE
    contradictions: str = ""


# ────────────────────── Protocol & Flow ────────────────────────────────

class ReviewProtocol(BaseModel):
    """PRISMA 2020 review protocol definition."""
    title: str = ""
    objective: str = ""
    pico_population: str = ""
    pico_intervention: str = ""
    pico_comparison: str = ""
    pico_outcome: str = ""
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    databases: list[str] = Field(default_factory=lambda: ["PubMed", "bioRxiv"])
    date_range_start: str = ""
    date_range_end: str = ""
    max_hops: int = 10
    registration_number: str = ""
    protocol_url: str = ""
    funding_sources: str = ""
    competing_interests: str = ""
    amendments: str = ""
    rob_tool: RoBTool = RoBTool.ROB_2
    review_id: str = ""  # URI for slr:SystematicReview; minted from UUID if empty
    # Cache / PostgreSQL settings
    pg_dsn: str = ""
    force_refresh: bool = False
    cache_threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    cache_ttl_days: int = Field(default=30, ge=0)
    # Custom per-article charting questions (answered into DataChartingRubric.custom_fields).
    # Leave empty to rely solely on the built-in sections A–G.
    charting_questions: list[str] = Field(
        default_factory=list,
        description=(
            "Domain-specific questions answered per included article. "
            "Each question becomes a key in DataChartingRubric.custom_fields. "
            "Example: ['What sequencing method was used?', 'Which diversity index was reported?']"
        ),
    )
    # Custom appraisal domain names (replaces the four default domain labels when provided).
    appraisal_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Override the four default appraisal domain names. "
            "Provide exactly 1–4 names; unspecified positions keep their defaults. "
            "Default domains: ['Participant and Sample Quality', 'Data Collection Quality', "
            "'Feature and Model Quality', 'Bias and Transparency']"
        ),
    )

    @property
    def pico_text(self) -> str:
        return (
            f"Population: {self.pico_population}\n"
            f"Intervention: {self.pico_intervention}\n"
            f"Comparison: {self.pico_comparison}\n"
            f"Outcome: {self.pico_outcome}"
        )

    @property
    def question(self) -> str:
        return self.objective or self.title


class PRISMAFlowCounts(BaseModel):
    """PRISMA 2020 flow diagram counts."""
    db_pubmed: int = 0
    db_biorxiv: int = 0
    db_related: int = 0
    db_hops: int = 0
    total_identified: int = 0
    duplicates_removed: int = 0
    after_dedup: int = 0
    screened_title_abstract: int = 0
    excluded_title_abstract: int = 0
    sought_fulltext: int = 0
    not_retrieved: int = 0
    assessed_eligibility: int = 0
    excluded_eligibility: int = 0
    excluded_reasons: dict[str, int] = Field(default_factory=dict)
    included_synthesis: int = 0


class ScreeningLogEntry(BaseModel):
    """A recorded screening decision."""
    pmid: str
    title: str = ""
    decision: ScreeningDecisionType
    reason: str = ""
    stage: ScreeningStage


class PRISMAReviewResult(BaseModel):
    """Complete result of a PRISMA systematic review."""
    research_question: str
    protocol: ReviewProtocol = Field(default_factory=ReviewProtocol)
    search_queries: list[str] = Field(default_factory=list)
    flow: PRISMAFlowCounts = Field(default_factory=PRISMAFlowCounts)
    included_articles: list[Article] = Field(default_factory=list)
    screening_log: list[ScreeningLogEntry] = Field(default_factory=list)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    synthesis_text: str = ""
    bias_assessment: str = ""
    limitations: str = ""
    grade_assessments: dict[str, GRADEAssessment] = Field(default_factory=dict)
    timestamp: str = ""
    # Cache provenance
    cache_hit: bool = False
    cache_similarity_score: float = 0.0
    cache_matched_criteria: dict = Field(default_factory=dict)


# ── Forward reference resolution ──
# Article references RiskOfBiasResult and StudyDataExtraction
# which are defined after Article, so we rebuild the model
Article.model_rebuild()
