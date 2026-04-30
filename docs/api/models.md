# Data Models

All models are Pydantic `BaseModel` subclasses and can be serialised with
`.model_dump()` / `.model_dump_json()`.

## Core Result

### `PRISMAReviewResult`

Top-level result returned by `pipeline.run()`.

```python
class PRISMAReviewResult(BaseModel):
    protocol: ReviewProtocol
    search_strategy: SearchStrategy
    articles: list[Article]           # all included articles with full annotation
    synthesis: SynthesisResult
    prisma_document: PrismaReview     # structured PRISMA 2020 document
    grounding: GroundingValidationResult
    plan: ReviewPlan | None
```

### `Article`

A single included study with all extracted annotations.

```python
class Article(BaseModel):
    pmid: str
    doi: str
    title: str
    abstract: str
    full_text: str
    authors: list[str]
    journal: str
    year: int
    evidence_spans: list[EvidenceSpan]
    data_extraction: StudyDataExtraction | None
    risk_of_bias: RiskOfBiasResult | None
    charting: DataChartingRubric | None
    appraisal: CriticalAppraisalRubric | None
    narrative_row: PRISMANarrativeRow | None
    grade: GRADEAssessment | None
    inclusion_status: InclusionStatus
    screening_reason: str
    relevance_score: float            # 0.0–1.0
```

## Evidence & Extraction

### `EvidenceSpan`

A single sentence of evidence, grounded to its source article.

```python
class EvidenceSpan(BaseModel):
    text: str                         # verbatim or close paraphrase
    claim: str                        # what the span supports
    source_pmid: str
    relevance: float                  # 0.0–1.0
    grounding_verdict: GroundingVerdict
```

### `StudyDataExtraction`

Structured per-study data.

```python
class StudyDataExtraction(BaseModel):
    study_design: str
    population_description: str
    sample_size: int | None
    intervention_description: str
    comparator_description: str
    primary_outcome: str
    effect_measure: str
    effect_estimate: str
    confidence_interval: str
    p_value: str
    conclusion: str
    limitations: str
```

## Risk of Bias

### `RiskOfBiasResult`

```python
class RiskOfBiasResult(BaseModel):
    tool: str                         # e.g. "RoB 2"
    domains: list[RoBDomainAssessment]
    overall_judgment: RoBJudgment
    summary: str
```

### `RoBDomainAssessment`

```python
class RoBDomainAssessment(BaseModel):
    domain: str
    judgment: RoBJudgment             # LOW | SOME | HIGH
    justification: str
```

## Appraisal & Charting

### `DataChartingRubric`

Seven-section (A–G) structured data extraction template.

### `CriticalAppraisalRubric`

Four-domain quality appraisal with per-item ratings and domain-level concerns.

```python
class CriticalAppraisalRubric(BaseModel):
    domains: list[DomainAppraisal]
    overall_concern: bool
    summary: str
```

### `GRADEAssessment`

```python
class GRADEAssessment(BaseModel):
    certainty: GRADECertainty         # HIGH | MODERATE | LOW | VERY_LOW
    rationale: str
    downgrade_reasons: list[str]
    upgrade_reasons: list[str]
```

## PRISMA Document

### `PrismaReview`

Complete structured PRISMA 2020 document.

```python
class PrismaReview(BaseModel):
    abstract: Abstract
    introduction: Introduction
    methods: str
    results: str
    discussion: Discussion
    conclusion: Conclusion
    prisma_flow: PrismaFlow           # N screened, included, excluded at each stage
    implications: Implications
```

## Search & Plan

### `SearchStrategy`

```python
class SearchStrategy(BaseModel):
    pubmed_queries: list[str]
    biorxiv_queries: list[str]
    mesh_terms: list[str]
    rationale: str
```

### `ReviewPlan`

Presented to the user (or callback) for approval before fetching articles.

```python
class ReviewPlan(BaseModel):
    search_strategy: SearchStrategy
    estimated_articles: int
    rationale: str
    iteration: int
```

## Compare Mode

### `CompareReviewResult`

```python
class CompareReviewResult(BaseModel):
    protocol: ReviewProtocol
    compare_models: list[str]
    model_results: list[ModelReviewRun]
    merged: MergedReviewResult
```

### `MergedReviewResult`

```python
class MergedReviewResult(BaseModel):
    consensus_synthesis: str
    field_agreement: dict[str, float]        # field → 0.0–1.0
    synthesis_divergences: list[SynthesisDivergence]
```

### `SynthesisDivergence`

```python
class SynthesisDivergence(BaseModel):
    topic: str
    positions: dict[str, str]               # model_name → position text
```
