"""
Pydantic models for PRISMA 2020 Systematic Review.

All data models use Pydantic v2 for validation, serialization, and
structured output from pydantic-ai agents.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional
from enum import Enum

from pydantic import BaseModel, Field, model_validator


# Built-in data charting section key → display title mapping (ordered A–G)
BUILTIN_SECTIONS: list[tuple[str, str]] = [
    ("A", "Publication Information"),
    ("B", "Study Design"),
    ("C", "Participants: Disordered Group"),
    ("D", "Participants: Healthy Controls"),
    ("E", "Data Collection"),
    ("F", "Features and Models"),
    ("G", "Synthesis Fields"),
]


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

class CriticalAppraisalItem(BaseModel):
    """Single item in a critical appraisal rubric."""
    item_text: str
    rating: str = ""
    notes: str = ""


class CriticalAppraisalDomain(BaseModel):
    """One domain in a critical appraisal rubric."""
    domain_name: str
    items: list[CriticalAppraisalItem] = Field(default_factory=list)
    overall_concern: str = ""


class CriticalAppraisalRubric(BaseModel):
    """Four-domain critical appraisal rubric completed per included article."""
    source_id: str = ""  # e.g., R-001 — matches DataChartingRubric.source_id
    domain_1_participant_quality: CriticalAppraisalDomain = Field(
        default_factory=lambda: CriticalAppraisalDomain(domain_name="Participant and Sample Quality")
    )
    domain_2_data_collection_quality: CriticalAppraisalDomain = Field(
        default_factory=lambda: CriticalAppraisalDomain(domain_name="Data Collection Quality")
    )
    domain_3_feature_model_quality: CriticalAppraisalDomain = Field(
        default_factory=lambda: CriticalAppraisalDomain(domain_name="Feature and Model Quality")
    )
    domain_4_bias_transparency: CriticalAppraisalDomain = Field(
        default_factory=lambda: CriticalAppraisalDomain(domain_name="Bias and Transparency")
    )


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
    critical_appraisal: Optional[CriticalAppraisalRubric] = None
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
    # Output formatting options
    target_audience: str = ""   # academic journal | policymaker | industry | thesis
    word_count_target: int = 8000
    citation_style: str = "APA 7"  # APA 7 | Vancouver | Harvard | IEEE | Chicago

    # Per-section output format control (US5)
    section_output_formats: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Per-section output format. Keys are section display names (e.g. 'Study Design'). "
            "Values: 'descriptive' | 'yes_no' | 'table' | 'bullet_list' | 'numeric'. "
            "Unknown keys are ignored with a warning. Invalid values raise ValueError."
        ),
    )
    rubric_section_config: list[RubricSectionConfig] = Field(
        default_factory=list,
        description=(
            "Full per-section config overriding title, display order, and format type. "
            "Takes precedence over section_output_formats for covered sections."
        ),
    )

    @model_validator(mode="after")
    def _validate_section_format_values(self) -> "ReviewProtocol":
        _valid = {"descriptive", "yes_no", "table", "bullet_list", "numeric"}
        invalid: list[str] = []
        for v in self.section_output_formats.values():
            if v not in _valid:
                invalid.append(f"section_output_formats value '{v}'")
        for cfg in self.rubric_section_config:
            if cfg.output_format not in _valid:
                invalid.append(f"rubric_section_config output_format '{cfg.output_format}'")
        if invalid:
            raise ValueError(f"Invalid output format values: {', '.join(invalid)}")
        return self

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
    prisma_review: Optional[PrismaReview] = None
    # Cache provenance
    cache_hit: bool = False
    cache_similarity_score: float = 0.0
    cache_matched_criteria: dict = Field(default_factory=dict)
    # Enhanced output (steps 13–18)
    data_charting_rubrics: list[DataChartingRubric] = Field(default_factory=list)
    narrative_rows: list[PRISMANarrativeRow] = Field(default_factory=list)
    critical_appraisals: list[CriticalAppraisalRubric] = Field(default_factory=list)
    grounding_validation: Optional[GroundingValidationResult] = Field(default=None)
    structured_abstract: str = ""
    introduction_text: str = ""
    conclusions_text: str = ""
    quality_checklist: dict[str, bool] = Field(default_factory=dict)


# ── Forward reference resolution ──
# Article references RiskOfBiasResult and StudyDataExtraction
# which are defined after Article, so we rebuild the model
Article.model_rebuild()


# ────────────────────── Enhanced Output Models ─────────────────────────────

class DataChartingRubric(BaseModel):
    """Data Charting Rubric with sections A-G for each included source."""
    source_id: str = ""  # e.g., M-001, R-001, N-001

    # Section A — Publication Information
    title: str = ""
    authors: str = ""
    year: str = ""
    journal_conference: str = ""
    doi: str = ""
    database_retrieved: str = ""
    disorder_cohort: str = ""
    primary_focus: str = ""

    # Section B — Study Design
    primary_goal: str = ""
    study_design: str = ""
    duration_frequency: str = ""
    subject_model: str = ""
    task_type: str = ""
    study_setting: str = ""
    country_region: str = ""

    # Section C — Participants: Disordered Group
    disorder_diagnosis: str = ""
    diagnosis_assessment: str = ""
    n_disordered: str = ""
    age_mean_sd: str = ""
    age_range: str = ""
    gender_distribution: str = ""
    comorbidities_included_excluded: str = ""
    medications_therapies: str = ""
    severity_levels: str = ""

    # Section D — Participants: Healthy Controls
    healthy_controls_included: str = ""
    healthy_status_confirmed: str = ""
    n_controls: str = ""
    age_mean_sd_controls: str = ""
    age_range_controls: str = ""
    gender_distribution_controls: str = ""
    age_matched: str = ""
    gender_matched: str = ""
    neurodevelopmentally_typical: str = ""

    # Section E — Data Collection
    data_types: str = ""
    tasks_performed: str = ""
    equipment_tools: str = ""
    new_dataset_contributed: str = ""
    dataset_openly_available: str = ""
    dataset_available_request: str = ""
    sensitive_data_anonymized: str = ""

    # Section F — Features and Models
    feature_types: str = ""
    specific_features: str = ""
    feature_extraction_tools: str = ""
    feature_importance_reported: str = ""
    importance_method: str = ""
    top_features_identified: str = ""
    feature_change_direction: str = ""
    model_category: str = ""
    specific_algorithms: str = ""
    validation_methodology: str = ""
    performance_metrics: str = ""
    key_performance_results: str = ""

    # Section G — Synthesis Fields
    summary_key_findings: str = ""
    features_associated_disorder: str = ""
    future_directions_recommended: str = ""
    reviewer_notes: str = ""

    # Answers to protocol.charting_questions (question text → extracted answer)
    custom_fields: dict[str, str] = Field(default_factory=dict)

    # Per-section structured outputs, populated by the data charting agent (US5)
    section_outputs: dict[str, RubricSectionOutput] = Field(default_factory=dict)


class PRISMANarrativeRow(BaseModel):
    """PRISMA-Style Narrative Row — condensed summary from charting data."""
    source_id: str = ""
    study_design_sample_dataset: str = ""
    methods: str = ""
    outcomes: str = ""
    key_limitations: str = ""
    relevance_notes: str = ""
    review_specific_questions: str = ""


class GroundingVerdict(str, Enum):
    """Grounding validation verdicts."""
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIABLE = "UNVERIFIABLE"


class ClaimType(str, Enum):
    """Types of atomic claims for grounding validation."""
    DESIGN = "Design"
    POPULATION = "Population"
    INTERVENTION = "Intervention"
    COMPARATOR = "Comparator"
    OUTCOME = "Outcome"
    TIMEPOINT = "Timepoint"
    EFFECT = "Effect"
    STATISTIC = "Statistic"
    CITATION = "Citation"
    QUALITATIVE = "Qualitative"
    OTHER = "Other"


class AtomicClaim(BaseModel):
    """An atomic claim extracted from AI-generated text for grounding validation."""
    id: str = Field(description="Unique identifier for the claim (e.g., 'C1', 'C2')")
    excerpt_text: str = Field(description="Verbatim text of the claim")
    claim_type: ClaimType = Field(description="Type of claim being made")
    cited_sources: list[str] = Field(default_factory=list, description="Citation keys referenced")
    source_span: str = Field(description="Supporting text from source corpus, or 'NO SUPPORTING SPAN FOUND'")
    verdict: GroundingVerdict = Field(description="Grounding validation verdict")
    rule_violated: Optional[str] = Field(default=None, description="Specific rule violated")
    discrepancy_note: Optional[str] = Field(default=None, description="Explanation of the discrepancy")
    suggested_correction: Optional[str] = Field(default=None, description="Suggested correction")


class GroundingValidationResult(BaseModel):
    """Complete grounding validation result for an AI-generated excerpt."""
    prerequisites_ok: bool = Field(description="Whether all required inputs are present")
    n_atomic_claims: int = Field(description="Total number of atomic claims decomposed")
    grounding_rate: float = Field(description="Proportion of SUPPORTED claims (0.0–1.0)")
    critical_error_count: int = Field(description="Number of CONTRADICTED claims")
    hallucinated_citation_count: int = Field(description="Number of hallucinated citations")
    overall_verdict: str = Field(description="PASS | REVISE | FAIL")
    claims: list[AtomicClaim] = Field(default_factory=list)
    unresolved_citations: list[str] = Field(default_factory=list)
    notes: Optional[str] = Field(default=None)


# Rebuild PRISMAReviewResult now that forward-ref types are defined
# raise_errors=False because PrismaReview is defined later; final rebuild at end of file resolves it
PRISMAReviewResult.model_rebuild(raise_errors=False)


# ────────────────── Plan Confirmation Models ───────────────────────────

class ReviewPlan(BaseModel):
    """Search plan presented to the user for confirmation before article retrieval."""
    research_question: str
    pubmed_queries: list[str]
    biorxiv_queries: list[str]
    mesh_terms: list[str]
    key_concepts: list[str]
    rationale: str
    iteration: int = 1


class PlanRejectedError(RuntimeError):
    """Raised when confirm_callback returns False (user explicitly rejects the plan)."""

    def __init__(self, iterations: int = 1):
        super().__init__(f"Plan rejected by user after {iterations} iteration(s)")
        self.iterations = iterations


class MaxIterationsReachedError(RuntimeError):
    """Raised when max_plan_iterations is exceeded without user approval."""

    def __init__(self, iterations: int, max_allowed: int):
        super().__init__(
            f"Plan confirmation limit ({max_allowed} iterations) reached without approval"
        )
        self.iterations = iterations
        self.max_allowed = max_allowed


# ────────────────────── Rich Synthesis Output Models ───────────────────────────


class OutputFormat(BaseModel):
    """Controls how the results section is rendered."""
    style: Literal["paragraph", "question_answer", "bullet_list", "table"] = "paragraph"
    notes: Optional[str] = None


class Abstract(BaseModel):
    """Five-part structured abstract (IMRAD convention)."""
    background: str
    objective: str
    methods: str
    results: str
    conclusion: str


class Introduction(BaseModel):
    """Four-section introduction establishing context and rationale."""
    background: str
    problem_statement: str
    research_gap: str
    objectives: str


class PrismaFlow(BaseModel):
    """PRISMA 2020 flow diagram counts (snapshot for PrismaReview)."""
    total_identified: int = 0
    duplicates_removed: int = 0
    screened: int = 0
    excluded: int = 0
    full_text_reviewed: int = 0
    final_included: int = 0


class DataExtractionField(BaseModel):
    """A single field in a data extraction schema."""
    field_name: str
    description: str
    options: Optional[list[str]] = None


class DataExtractionSchema(BaseModel):
    """One section of the data extraction schema."""
    section_name: str
    fields: list[DataExtractionField]


class Methods(BaseModel):
    """Methods reproducibility record (assembled deterministically)."""
    search_strategy: str
    study_selection: PrismaFlow
    inclusion_criteria: list[str]
    exclusion_criteria: list[str]
    data_extraction_schema: list[DataExtractionSchema]
    data_extraction: list[StudyDataExtractionReport] = Field(default_factory=list)
    quality_assessment: str


class SourceMetadata(BaseModel):
    """Source identification and provenance metadata for an included study."""
    source_id: str
    title: str
    authors: str
    year: int
    journal_or_conference: Optional[str] = None
    doi: Optional[str] = None
    database_retrieved_from: str
    disorder_cohort: Optional[str] = None
    primary_focus: Optional[str] = None


class StudyDesign(BaseModel):
    """Study design attributes for an included study."""
    primary_study_goal: str
    study_design: str
    longitudinal_duration: Optional[str] = None
    longitudinal_frequency: Optional[str] = None
    subject_model: Optional[str] = None
    task_type: Optional[str] = None
    study_setting: Optional[str] = None
    country_or_region: Optional[str] = None


class ExtractedStudy(BaseModel):
    """Per-article structured data combining metadata and design."""
    metadata: SourceMetadata
    design: StudyDesign


class ParagraphBlock(BaseModel):
    """A single paragraph block in narrative rendering."""
    heading: Optional[str] = None
    text: str


class QAItem(BaseModel):
    """A question-answer pair in Q&A rendering."""
    question: str
    answer: str


class Theme(BaseModel):
    """A cross-study analytical grouping from thematic synthesis."""
    theme_name: str
    description: str
    supporting_studies: list[str]
    key_findings: list[str]


class QuantitativeAnalysis(BaseModel):
    """Optional meta-analytic summary when ≥3 studies have numeric outcomes."""
    effect_size: Optional[str] = None
    confidence_intervals: Optional[str] = None
    heterogeneity: Optional[str] = None


class BiasAssessment(BaseModel):
    """Cross-study risk-of-bias summary."""
    overall_quality: str
    common_biases: list[str]
    risk_level: str  # "low" | "moderate" | "high"


class Results(BaseModel):
    """Analytical output combining PRISMA flow, themes, per-study data, and bias."""
    output_format: OutputFormat
    prisma_flow_summary: PrismaFlow
    extracted_studies: Optional[list[ExtractedStudy]] = None
    paragraph_summary: Optional[list[ParagraphBlock]] = None
    question_answer_summary: Optional[list[QAItem]] = None
    themes: list[Theme]
    quantitative_analysis: Optional[QuantitativeAnalysis] = None
    bias_assessment: BiasAssessment


class Implications(BaseModel):
    """Implications from the discussion section."""
    clinical: str
    policy: str
    research: str


class Discussion(BaseModel):
    """Interpretive discussion placing findings in context."""
    summary_of_findings: str
    interpretation: str
    comparison_with_literature: str
    implications: Implications
    limitations: str


class Conclusion(BaseModel):
    """Terminal synthesis with takeaways, recommendations, and future directions."""
    key_takeaways: str
    recommendations: str
    future_research: str


class OptionalSection(BaseModel):
    """Optional extended metadata."""
    evidence_gaps: Optional[str] = None
    suggested_visualizations: Optional[list[str]] = None


class ThematicSynthesisResult(BaseModel):
    """Intermediate output from the thematic synthesis agent."""
    themes: list[Theme]
    paragraph_summary: Optional[list[ParagraphBlock]] = None
    question_answer_summary: Optional[list[QAItem]] = None
    bias_assessment: BiasAssessment


# ────────────────── Per-Rubric Section Format Models ───────────────────────────

SECTION_FORMAT = Literal["descriptive", "yes_no", "table", "bullet_list", "numeric"]


class RubricSectionOutput(BaseModel):
    """Per-section extraction result stored on DataChartingRubric.section_outputs."""
    format_used: SECTION_FORMAT
    formatted_answer: str
    section_summary: Optional[str] = None

    @model_validator(mode="after")
    def _require_summary_for_structured_formats(self) -> "RubricSectionOutput":
        if self.format_used in {"table", "bullet_list", "numeric"} and not self.section_summary:
            raise ValueError(
                f"section_summary is required when format_used is '{self.format_used}'"
            )
        return self


class RubricSectionConfig(BaseModel):
    """Caller-supplied configuration for a single rubric section."""
    section_key: str
    section_name: str
    order: int
    output_format: SECTION_FORMAT = "descriptive"


class StudyDataExtractionReport(BaseModel):
    """Per-study container in PrismaReview.methods.data_extraction."""
    source_id: str
    sections: dict[str, RubricSectionOutput] = Field(default_factory=dict)


class PrismaReview(BaseModel):
    """Root document for a completed PRISMA 2020 systematic review."""
    title: str
    abstract: Abstract
    introduction: Introduction
    methods: Methods
    results: Results
    discussion: Discussion
    conclusion: Conclusion
    references: list[str]
    optional: Optional[OptionalSection] = None


# Rebuild PRISMAReviewResult one final time to resolve PrismaReview forward reference
PRISMAReviewResult.model_rebuild()
