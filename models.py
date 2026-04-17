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
    # Section 1 required inputs (per brief)
    grey_literature_sources: list[str] = Field(default_factory=list)
    target_audience: str = ""   # academic journal | policymaker | industry | thesis
    word_count_target: int = 8000
    citation_style: str = "APA 7"  # APA 7 | Vancouver | Harvard | IEEE | Chicago
    languages: list[str] = Field(default_factory=lambda: ["English"])
    protocol_overrides: str = ""  # custom charting fields / cohort taxonomies

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
    # Enhanced output formats
    data_charting_rubrics: list[DataChartingRubric] = Field(default_factory=list)
    narrative_rows: list[PRISMANarrativeRow] = Field(default_factory=list)
    critical_appraisals: list[CriticalAppraisalRubric] = Field(default_factory=list)
    grounding_validation: Optional[GroundingValidationResult] = Field(default=None, description="Grounding validation of AI-generated synthesis")
    # Full document sections (brief §2.2, §2.3, §2.7)
    structured_abstract: str = ""
    introduction_text: str = ""
    conclusions_text: str = ""
    quality_checklist: dict[str, bool] = Field(default_factory=dict)


# ────────────────────── Enhanced Output Models ─────────────────────────

class DataChartingRubric(BaseModel):
    """Data Charting Rubric with sections A-G for each included source."""
    source_id: str  # e.g., M-001, R-001, N-001

    # Section A — Publication Information
    title: str = ""
    authors: str = ""  # Last name, First initial
    year: str = ""
    journal_conference: str = ""
    doi: str = ""
    database_retrieved: str = ""  # PubMed, Scopus, etc.
    disorder_cohort: str = ""  # One of the five Bridge2AI cohorts
    primary_focus: str = ""  # disorder-focused vs. technology-focused

    # Section B — Study Design
    primary_goal: str = ""  # classification, severity assessment, etc.
    study_design: str = ""  # cross-sectional / longitudinal
    duration_frequency: str = ""  # if longitudinal
    subject_model: str = ""  # within / between / mixed
    task_type: str = ""  # classification / regression / both
    study_setting: str = ""  # clinical / lab / remote
    country_region: str = ""

    # Section C — Participants: Disordered Group
    disorder_diagnosis: str = ""
    diagnosis_assessment: str = ""  # MDS-UPDRS, DSM-5, etc.
    n_disordered: str = ""
    age_mean_sd: str = ""
    age_range: str = ""
    gender_distribution: str = ""
    comorbidities_included_excluded: str = ""
    medications_therapies: str = ""
    severity_levels: str = ""  # Mild / Moderate / Severe / Mixed / Not Reported

    # Section D — Participants: Healthy Controls
    healthy_controls_included: str = ""  # Y/N
    healthy_status_confirmed: str = ""
    n_controls: str = ""
    age_mean_sd_controls: str = ""
    age_range_controls: str = ""
    gender_distribution_controls: str = ""
    age_matched: str = ""  # Y/N/NR
    gender_matched: str = ""  # Y/N/NR
    neurodevelopmentally_typical: str = ""  # Y/N/NR

    # Section E — Data Collection
    data_types: str = ""  # audio, video, text, physiological
    tasks_performed: str = ""  # sustained vowel, read speech, etc.
    equipment_tools: str = ""
    new_dataset_contributed: str = ""  # Y/N
    dataset_openly_available: str = ""  # Y/N/NR
    dataset_available_request: str = ""  # Y/N/NR
    sensitive_data_anonymized: str = ""  # Y/N/NR

    # Section F — Features and Models
    feature_types: str = ""  # acoustic / linguistic / articulatory / DNN embeddings / combination
    specific_features: str = ""  # MFCCs, jitter, shimmer, F0, HNR
    feature_extraction_tools: str = ""  # openSMILE, torchaudio, librosa
    feature_importance_reported: str = ""  # Y/N
    importance_method: str = ""  # SHAP, permutation, correlation
    top_features_identified: str = ""
    feature_change_direction: str = ""  # Increase / Decrease / Mixed / NR
    model_category: str = ""  # statistical / classical ML / deep learning
    specific_algorithms: str = ""  # SVM, Random Forest, LSTM, wav2vec
    validation_methodology: str = ""  # train/test, k-fold CV, LOOCV, held-out test
    performance_metrics: str = ""  # Accuracy, AUC, F1, RMSE, R²
    key_performance_results: str = ""

    # Section G — Synthesis Fields
    summary_key_findings: str = ""  # 1–2 sentences
    features_associated_disorder: str = ""
    future_directions_recommended: str = ""
    reviewer_notes: str = ""


class PRISMANarrativeRow(BaseModel):
    """PRISMA-Style Narrative Row — condensed summary from charting data."""
    source_id: str
    study_design_sample_dataset: str = ""  # Drawn from Sections B, C, D, E
    methods: str = ""  # Drawn from Sections E, F (feature extraction, model, validation)
    outcomes: str = ""  # Drawn from Section F (key performance results), Section G (summary of findings)
    key_limitations: str = ""  # Drawn from Section G (reviewer notes) + appraisal domains
    relevance_notes: str = ""  # Drawn from Section A (disorder cohort, primary focus), Section G
    review_specific_questions: str = ""  # Customized per review protocol


class CriticalAppraisalItem(BaseModel):
    """Single item in critical appraisal rubric."""
    item_text: str
    rating: str  # Yes / Partial / No / Not Reported / N/A
    notes: str = ""


class CriticalAppraisalDomain(BaseModel):
    """Domain in critical appraisal rubric."""
    domain_name: str
    items: list[CriticalAppraisalItem] = Field(default_factory=list)
    overall_concern: str = ""  # Low / Some / High


class CriticalAppraisalRubric(BaseModel):
    """Critical Appraisal Rubric completed by human reviewer."""
    source_id: str
    domain_1_participant_quality: CriticalAppraisalDomain = Field(default_factory=lambda: CriticalAppraisalDomain(domain_name="Participant and Sample Quality"))
    domain_2_data_collection_quality: CriticalAppraisalDomain = Field(default_factory=lambda: CriticalAppraisalDomain(domain_name="Data Collection Quality"))
    domain_3_feature_model_quality: CriticalAppraisalDomain = Field(default_factory=lambda: CriticalAppraisalDomain(domain_name="Feature and Model Quality"))
    domain_4_bias_transparency: CriticalAppraisalDomain = Field(default_factory=lambda: CriticalAppraisalDomain(domain_name="Bias and Transparency"))

    @property
    def overall_concern_score(self) -> str:
        """Calculate overall concern based on domain scores."""
        domains = [self.domain_1_participant_quality, self.domain_2_data_collection_quality,
                  self.domain_3_feature_model_quality, self.domain_4_bias_transparency]
        concerns = [d.overall_concern for d in domains if d.overall_concern]
        if "High" in concerns:
            return "High"
        elif "Some" in concerns:
            return "Some"
        elif concerns and all(c == "Low" for c in concerns):
            return "Low"
        return "Not Assessed"


class ScoringConvention(BaseModel):
    """Scoring conventions and definitions."""
    item_level_definitions: dict[str, str] = Field(default_factory=dict)
    domain_level_definitions: dict[str, str] = Field(default_factory=dict)
    process_guidelines: str = ""


# ────────────────────── Grounding Validation Models ─────────────────────────

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
    excerpt_text: str = Field(description="Verbatim text of the claim from the target excerpt")
    claim_type: ClaimType = Field(description="Type of claim being made")
    cited_sources: list[str] = Field(default_factory=list, description="Citation keys referenced for this claim")
    source_span: str = Field(description="Supporting text from source corpus, or 'NO SUPPORTING SPAN FOUND'")
    verdict: GroundingVerdict = Field(description="Grounding validation verdict")
    rule_violated: Optional[str] = Field(default=None, description="Specific rule violated (e.g., 'R-NUM-1')")
    discrepancy_note: Optional[str] = Field(default=None, description="Explanation of the discrepancy")
    suggested_correction: Optional[str] = Field(default=None, description="Suggested correction grounded in source")


class GroundingValidationResult(BaseModel):
    """Complete grounding validation result for an AI-generated excerpt."""
    prerequisites_ok: bool = Field(description="Whether all required inputs (corpus, citation_map, excerpt) are present")
    n_atomic_claims: int = Field(description="Total number of atomic claims decomposed")
    grounding_rate: float = Field(description="Proportion of claims that are SUPPORTED (0.0 to 1.0)")
    critical_error_count: int = Field(description="Number of CONTRADICTED claims involving critical elements")
    hallucinated_citation_count: int = Field(description="Number of hallucinated citations")
    overall_verdict: str = Field(description="PASS | REVISE | FAIL")
    claims: list[AtomicClaim] = Field(default_factory=list, description="Detailed validation results for each claim")
    unresolved_citations: list[str] = Field(default_factory=list, description="Citation keys not found in citation map")
    notes: Optional[str] = Field(default=None, description="Free-text notes on systemic issues")


class GroundingValidationDeps(BaseModel):
    """Dependencies for grounding validation."""
    target_excerpt: str = Field(description="The AI-generated text to validate")
    corpus_documents: dict[str, str] = Field(description="Citation key -> full text mapping")
    citation_map: dict[str, str] = Field(description="Citation key -> document identifier mapping")


# ── Forward reference resolution ──
# Article references RiskOfBiasResult and StudyDataExtraction
# which are defined after Article, so we rebuild the model
Article.model_rebuild()
