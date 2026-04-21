# PRISMA Agent — Pydantic AI Systematic Review

A standalone, agent-based systematic literature review tool following **PRISMA 2020** guidelines. Built with [pydantic-ai](https://ai.pydantic.dev/) for structured LLM interactions and typed outputs via [OpenRouter](https://openrouter.ai/).

## Architecture

```
prisma-review-agent/
├── models.py           # Pydantic v2 models (Article, Protocol, Evidence, GRADE, etc.)
├── clients.py          # HTTP clients: PubMed (NCBI E-utilities), bioRxiv, SQLite cache
├── agents.py           # 12 pydantic-ai agents with typed outputs + runner functions
├── evidence.py         # Evidence extraction + source grounding validation gate
├── validation.py       # Source grounding validator — rapidfuzz fuzzy matching
├── pipeline.py         # Async orchestrator — 16-step PRISMA pipeline with cache
├── compare.py          # Multi-model compare mode — parallel runs + consensus synthesis
├── export.py           # Export: Markdown, JSON, BibTeX, CSV formats
├── main.py             # Standalone CLI with argparse + interactive mode
└── prisma_review_agent/
    └── cache/          # PostgreSQL cache sub-package
        ├── models.py        # CacheEntry, SimilarityConfig, StoredArticle, PipelineCheckpoint
        ├── similarity.py    # SHA-256 fingerprinting + weighted fuzzy scoring
        ├── store.py         # CacheStore — async PostgreSQL CRUD
        ├── article_store.py # ArticleStore — article persistence + full-text search
        ├── skill.py         # pydantic-ai CacheAgent with @agent.tool tools
        ├── admin.py         # list/inspect/clear cache entries
        └── migrations/001_initial.sql
```

### Design Principles

- **Agent-per-task**: Each PRISMA step that requires LLM reasoning has a dedicated pydantic-ai `Agent` with a typed `output_type`. No raw string parsing — the LLM returns validated Pydantic models.
- **No hardcoded heuristics**: Evidence extraction, screening, bias assessment, and synthesis are all handled by specialized LLM agents. No keyword lists or regex scoring.
- **Source grounding**: Every extracted evidence span is verified against its source article using rapidfuzz fuzzy matching before being included. Ungrounded spans are silently dropped.
- **Typed throughout**: Every data structure is a Pydantic `BaseModel` with validation. Structured outputs from agents are parsed and validated automatically by pydantic-ai.
- **PostgreSQL result cache**: Reviews with ≥ 95% similar criteria are served from cache in seconds instead of minutes. All fetched articles are indexed for future source reuse.
- **Parallel per-article processing**: Steps 7–15 (T/A screening, FT screening, evidence extraction, data extraction, RoB, charting, appraisal, narrative rows) run with configurable `asyncio` concurrency in **both** the standard pipeline and compare mode. Two shared module-level helpers (`_parallel_ta_screening`, `_parallel_ft_screening` in `pipeline.py`) are reused by both paths, keeping the parallelism logic in one place. At the default of 5 parallel LLM calls, a 100-article review that would take ~70 min sequentially completes in ~15 min. Set `--concurrency 10` for larger reviews.
- **Standalone**: No web framework dependency. PostgreSQL is optional — the pipeline degrades gracefully without it.

## Installation

### From PyPI (recommended)

```bash
pip install prisma-review-agent
```

### From source

```bash
git clone https://github.com/tekrajchhetri/prisma-review-agent.git
cd prisma-review-agent
python -m pip install uv
uv install
```

## Quick Start

### Set API Key

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."

# Optional: higher PubMed rate limits (10 req/s vs 3 req/s)
export NCBI_API_KEY="your-ncbi-key"
```

Alternatively, pass the key inline with `--api-key` (takes precedence over the env var):

```bash
prisma-review --title "CRISPR gene therapy" --api-key "sk-or-v1-..."
```

### CLI — installed package

After `pip install prisma-review-agent` the `prisma-review` command is available globally:

```bash
# Simple review
prisma-review \
  --title "CRISPR gene therapy efficacy" \
  --inclusion "Clinical trials, human subjects, English" \
  --exclusion "Animal-only studies, reviews, commentaries"

# Full PICO specification
prisma-review \
  --title "GLP-1 agonists for type 2 diabetes: a systematic review" \
  --objective "Evaluate efficacy of GLP-1 RAs vs placebo for glycemic control" \
  --population "Adults with type 2 diabetes mellitus" \
  --intervention "GLP-1 receptor agonists" \
  --comparison "Placebo or standard care" \
  --outcome "HbA1c reduction, weight change, adverse events" \
  --inclusion "RCTs, English, 2019-2024, peer-reviewed" \
  --exclusion "Case reports, editorials, conference abstracts" \
  --model "anthropic/claude-sonnet-4" \
  --max-results 30 \
  --hops 2 \
  --rob-tool "RoB 2" \
  --extract-data \
  --concurrency 10 \
  --export md json bib

# Interactive mode
prisma-review --interactive
```

### CLI — from source (without installing)

```bash
python main.py --title "..." --interactive
```

### Plan Confirmation (CLI)

By default, the pipeline pauses after generating the search strategy, shows the plan, and waits for your input before fetching any articles.

```bash
# Default — prompts for confirmation when running in a terminal
prisma-review \
  --title "CRISPR gene therapy efficacy" \
  --inclusion "Clinical trials, human subjects" \
  --exclusion "Animal-only studies"

# Auto mode — no prompt (for scripts, CI, batch jobs)
prisma-review \
  --title "CRISPR gene therapy efficacy" \
  --auto \
  --export ttl jsonld

# Limit re-generation attempts to 2
prisma-review \
  --title "CRISPR gene therapy efficacy" \
  --max-plan-iterations 2
```

**Confirmation prompt:**

```
══════════════════════════════════════════════════
  Generated Search Plan (Iteration 1)
══════════════════════════════════════════════════
Research question: CRISPR gene therapy efficacy in clinical trials

PubMed queries (3):
  1. CRISPR gene therapy clinical trials efficacy
  2. CRISPR-Cas9 human trials outcomes
  3. gene editing therapy safety efficacy RCT

MeSH terms: CRISPR-Cas Systems, Gene Therapy, Clinical Trials as Topic
Rationale: Focused on clinical evidence to match inclusion criteria...
══════════════════════════════════════════════════
Confirm plan? [yes / no / <feedback>]:
```

- Press **Enter** or type **yes** → proceed to article retrieval
- Type **no** → halt with rejection message and exit 1
- Type feedback (e.g., `"add pediatric studies"`) → plan is re-generated with your input

### Python API

```python
import asyncio
from pathlib import Path
from prisma_review_agent import (
    PRISMAReviewPipeline,
    ReviewProtocol,
    RoBTool,
    to_markdown,
    to_json,
)

protocol = ReviewProtocol(
    title="Gut microbiome and depression",
    objective="Examine the relationship between gut microbiota composition and depressive disorders",
    pico_population="Adults with major depressive disorder",
    pico_intervention="Gut microbiome profiling",
    pico_comparison="Healthy controls",
    pico_outcome="Microbiome diversity, specific taxa abundance",
    inclusion_criteria="Human studies, English, 2018-2024",
    exclusion_criteria="Animal studies, reviews, case reports",
    max_hops=10,
    rob_tool=RoBTool.NEWCASTLE_OTTAWA,
    article_concurrency=10,   # parallel LLM calls per article step (default: 5)

    # Domain-specific charting questions — answered per included article and stored
    # in DataChartingRubric.custom_fields (question text → extracted answer).
    # Leave out entirely to use only the built-in sections A–G.
    charting_questions=[
        "What sequencing method was used (16S rRNA, shotgun metagenomics, or other)?",
        "Which taxonomic level was the primary analysis performed at?",
        "What alpha-diversity indices were reported (Shannon, Simpson, Chao1, …)?",
        "Was the gut-brain axis or HPA axis explicitly discussed?",
        "Were dietary intake data collected and reported?",
    ],

    # Override the four default appraisal domain names for this review type.
    # Unspecified positions (here: 3 and 4) keep their defaults.
    appraisal_domains=[
        "Participant Recruitment and Microbiome Sampling Quality",
        "Sequencing and Bioinformatic Pipeline Quality",
    ],
)

async def run():
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        model_name="anthropic/claude-sonnet-4",
        protocol=protocol,
        max_per_query=25,
        related_depth=1,
    )
    result = await pipeline.run()

    # Export
    Path("review.md").write_text(to_markdown(result))
    Path("review.json").write_text(to_json(result))

    # Access structured data
    print(f"Included: {result.flow.included_synthesis} studies")
    for article in result.included_articles:
        rob = article.risk_of_bias.overall.value if article.risk_of_bias else "?"
        print(f"  [{article.pmid}] {article.authors} ({article.year}) — RoB: {rob}")

    for span in result.evidence_spans[:5]:
        print(f"  Evidence [{span.paper_pmid}]: {span.text[:100]}...")

asyncio.run(run())
```

### Python API — Plan Confirmation

Use the `confirm_callback` parameter to intercept the generated plan from any Python environment (scripts, Jupyter, web APIs) without any terminal dependency.

```python
import asyncio
from prisma_review_agent.models import ReviewPlan, PlanRejectedError, MaxIterationsReachedError
from prisma_review_agent.pipeline import PRISMAReviewPipeline
from prisma_review_agent.models import ReviewProtocol

protocol = ReviewProtocol(
    title="CRISPR gene therapy efficacy",
    inclusion_criteria="Clinical trials, human subjects",
    exclusion_criteria="Animal-only studies",
)

def confirm(plan: ReviewPlan) -> bool | str:
    """Inspect the plan and return True, False, or feedback text."""
    print(f"Iteration {plan.iteration}: {len(plan.pubmed_queries)} PubMed queries")
    for q in plan.pubmed_queries:
        print(f"  - {q}")
    answer = input("Approve? [yes/no/feedback]: ").strip()
    if answer.lower() in ("yes", "y", ""):
        return True
    if answer.lower() in ("no", "abort"):
        return False
    return answer  # feedback string → triggers re-generation

async def main():
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        model_name="anthropic/claude-sonnet-4",
        protocol=protocol,
    )
    try:
        result = await pipeline.run(
            confirm_callback=confirm,
            max_plan_iterations=3,
        )
        print(f"Review complete: {result.flow.included_synthesis} studies included")
    except PlanRejectedError as e:
        print(f"Stopped: {e}")
    except MaxIterationsReachedError as e:
        print(f"Too many iterations: {e}")

asyncio.run(main())
```

**Auto mode** — skip confirmation entirely:

```python
# No confirmation prompts; runs end-to-end
result = await pipeline.run(auto_confirm=True)
```

### Multi-Model Compare Mode

Run the same protocol through two or more LLMs in parallel. Article acquisition (PubMed/bioRxiv search, deduplication) runs once; all LLM-dependent steps (screening, evidence extraction, data extraction, RoB, charting, appraisal, narrative) run independently per model using the same shared parallel helpers as the standard pipeline — steps 7–15 are fully parallel within each model's pipeline. Results are merged into a single `CompareReviewResult` with per-field agreement indicators and an LLM-generated consensus synthesis.

#### Plan confirmation and strategy revision in compare mode

The "Generated Search Plan" review/approve/revise gate works **identically** in compare mode. Article acquisition (search strategy generation, PubMed/bioRxiv search, deduplication) runs **once** and is shared across all models. The plan confirmation loop fires during that shared step — before any per-model LLM work begins.

**CLI — plan prompt in compare mode**

Omit `--auto` to see the prompt. The same three-way response applies:

```bash
prisma-review \
  --title "CRISPR gene therapy efficacy" \
  --inclusion "Clinical trials, human subjects" \
  --exclusion "Animal-only studies" \
  --compare-models anthropic/claude-sonnet-4 openai/gpt-4o
```

```
══════════════════════════════════════════════════
  Generated Search Plan (Iteration 1)
══════════════════════════════════════════════════
Research question: CRISPR gene therapy efficacy in clinical trials

PubMed queries (3):
  1. CRISPR gene therapy clinical trials efficacy
  2. CRISPR-Cas9 human trials outcomes
  3. gene editing therapy safety efficacy RCT

MeSH terms: CRISPR-Cas Systems, Gene Therapy, Clinical Trials as Topic
Rationale: Focused on clinical evidence to match inclusion criteria...
══════════════════════════════════════════════════
Confirm plan? [yes / no / <feedback>]:
```

- Press **Enter** or type **yes** → proceed; the approved strategy is used for the shared article fetch, then all models run in parallel
- Type **no** → halt with `PlanRejectedError` before any search is executed
- Type feedback (e.g., `"add pediatric CRISPR trials and broaden to gene editing"`) → the search strategy is **revised** and the updated plan is shown again for re-approval (up to `--max-plan-iterations` rounds)

**Revised plan after feedback:**

```
══════════════════════════════════════════════════
  Generated Search Plan (Iteration 2)
══════════════════════════════════════════════════
PubMed queries (4):
  1. CRISPR gene therapy pediatric clinical trials efficacy
  2. CRISPR-Cas9 children adolescents outcomes
  3. gene editing therapy sickle cell beta-thalassemia pediatric
  4. base editing clinical trial safety efficacy

Rationale: Expanded to include pediatric subgroups and broader gene editing
           approaches as requested...
══════════════════════════════════════════════════
Confirm plan? [yes / no / <feedback>]:
```

**CLI — skip confirmation (unattended / CI)**

```bash
prisma-review \
  --title "CRISPR gene therapy efficacy" \
  --compare-models anthropic/claude-sonnet-4 openai/gpt-4o \
  --auto
```

Requires at least 2 models; up to 5 supported per run.

#### Python API — compare mode

```python
import asyncio
from pathlib import Path
from prisma_review_agent import (
    PRISMAReviewPipeline, ReviewProtocol,
    to_compare_markdown, to_compare_json,
    to_compare_charting_markdown, to_compare_charting_json,
)
from prisma_review_agent.models import ReviewPlan, PlanRejectedError, MaxIterationsReachedError

protocol = ReviewProtocol(
    title="CRISPR gene therapy efficacy",
    inclusion_criteria="Clinical trials, human subjects, English",
    exclusion_criteria="Animal-only studies, reviews",
)

def confirm_and_revise(plan: ReviewPlan) -> bool | str:
    """Called once per iteration. Return True to approve, False to abort,
    or a feedback string to revise the strategy and re-prompt."""
    print(f"\n--- Search Plan (iteration {plan.iteration}) ---")
    print(f"Research question: {plan.research_question}")
    print(f"PubMed queries ({len(plan.pubmed_queries)}):")
    for q in plan.pubmed_queries:
        print(f"  - {q}")
    if plan.mesh_terms:
        print(f"MeSH: {', '.join(plan.mesh_terms)}")
    print(f"Rationale: {plan.rationale[:120]}...")
    answer = input("Approve? [yes / no / feedback to revise]: ").strip()
    if answer.lower() in ("", "yes", "y"):
        return True           # approved — proceed with shared article acquisition
    if answer.lower() in ("no", "abort"):
        return False          # rejected — raises PlanRejectedError
    return answer             # feedback string — strategy is re-generated and callback fires again

async def run():
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        model_name="anthropic/claude-sonnet-4",  # used for search strategy generation
        protocol=protocol,
    )

    try:
        compare_result = await pipeline.run_compare(
            models=["anthropic/claude-sonnet-4", "openai/gpt-4o"],
            auto_confirm=False,                  # enable plan review + revision
            confirm_callback=confirm_and_revise, # same interface as pipeline.run()
            max_plan_iterations=3,               # max revision rounds (default 3)
            consensus_model="anthropic/claude-sonnet-4",
            assemble_timeout=3600.0,
        )
    except PlanRejectedError:
        print("Review aborted — plan rejected by user.")
        return
    except MaxIterationsReachedError as e:
        print(f"Stopped after {e.max_allowed} revision rounds without approval.")
        return

    # Per-model and merged exports
    Path("compare.md").write_text(to_compare_markdown(compare_result))
    Path("compare.json").write_text(to_compare_json(compare_result))
    Path("charting_compare.md").write_text(to_compare_charting_markdown(compare_result))

    # Access structured results
    for run in compare_result.model_results:
        if run.succeeded:
            print(f"{run.model_name}: {len(run.result.included_articles or [])} included")
        else:
            print(f"{run.model_name}: FAILED — {run.error}")

    print("\nConsensus:")
    print(compare_result.merged.consensus_synthesis[:300])

    print(f"\nDivergences: {len(compare_result.merged.synthesis_divergences)}")
    for div in compare_result.merged.synthesis_divergences:
        print(f"  [{div.topic}]")
        for model, pos in div.positions.items():
            print(f"    {model}: {pos[:80]}")

    # Field-level agreement
    agreed = sum(1 for fa in compare_result.merged.field_agreement.values() if fa.agreed)
    total = len(compare_result.merged.field_agreement)
    print(f"\nField agreement: {agreed}/{total} fields agreed")

asyncio.run(run())
```

#### `CompareReviewResult` structure

| Attribute | Type | Description |
|---|---|---|
| `compare_models` | `list[str]` | Ordered list of model names used |
| `model_results` | `list[ModelReviewRun]` | One entry per model; `.succeeded` / `.result` / `.error` |
| `merged.consensus_synthesis` | `str` | LLM-generated prose summarising agreed findings |
| `merged.synthesis_divergences` | `list[SynthesisDivergence]` | Per-topic disagreements with per-model positions |
| `merged.field_agreement` | `dict[str, FieldAgreement]` | Key: `"{source_id}::{section_key}::{field_name}"` |
| `protocol` | `ReviewProtocol` | Shared protocol used for all model runs |

Partial failures are handled gracefully: if one model fails, its `ModelReviewRun` has `error` set and `result=None`; the remaining models' results and the consensus synthesis (if ≥2 succeeded) are still returned.

### Structured Report Output (`result.prisma_review`)

Every successful run with at least one included study produces a `PrismaReview` object on `result.prisma_review`. It is a complete, publication-ready PRISMA 2020 document with all major sections as typed Pydantic models.

```python
import asyncio
from prisma_review_agent.models import ReviewProtocol
from prisma_review_agent.pipeline import PRISMAReviewPipeline

async def run():
    protocol = ReviewProtocol(
        title="Machine learning for ADHD diagnosis",
        objective="Evaluate ML classifiers for ADHD detection from EEG signals",
        inclusion_criteria="EEG studies, human subjects, ML classifier reported",
        exclusion_criteria="Animal studies, reviews without primary data",
    )
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        protocol=protocol,
        enable_cache=False,
    )
    result = await pipeline.run(auto_confirm=True)

    review = result.prisma_review
    if review:
        # Access structured sections
        print(review.abstract.background)
        print(review.abstract.conclusion)
        print(f"{len(review.results.themes)} themes identified")
        for theme in review.results.themes:
            print(f"  - {theme.theme_name}: {', '.join(theme.key_findings[:2])}")
        print(review.conclusion.recommendations)

asyncio.run(run())
```

**Per-study structured data:**

```python
review = result.prisma_review
if review and review.results.extracted_studies:
    for study in review.results.extracted_studies:
        print(f"[{study.metadata.source_id}] {study.metadata.title[:60]}")
        print(f"  Design: {study.design.study_design}")
        print(f"  Country: {study.design.country_or_region}")
        print(f"  Year: {study.metadata.year}")
```

**Configurable rendering format:**

Pass `output_synthesis_style` to control how results are rendered. Default is `"paragraph"`; also supports `"question_answer"`, `"bullet_list"`, `"table"`.

```python
result = await pipeline.run(
    auto_confirm=True,
    output_synthesis_style="question_answer",
)
review = result.prisma_review
for qa in (review.results.question_answer_summary or []):
    print(f"Q: {qa.question}")
    print(f"A: {qa.answer}\n")
```

**Backward compatibility:** All existing flat fields (`result.synthesis_text`, `result.structured_abstract`, `result.introduction_text`, `result.conclusions_text`) are automatically backfilled from the structured report and continue to work unchanged.

### Per-Rubric Section Output Formats

Configure how each data charting section (A–G + custom) renders its answer. Five format types are supported: `descriptive` (default), `yes_no`, `table`, `bullet_list`, `numeric`. For `table`, `bullet_list`, and `numeric` sections a prose summary is also generated automatically.

**Simple API — `section_output_formats` dict:**

```python
from prisma_review_agent.models import ReviewProtocol

protocol = ReviewProtocol(
    title="Digital biomarkers for Parkinson's disease",
    objective="Identify ML-based biomarkers from wearable sensor data",
    inclusion_criteria="Wearable sensor studies, PD patients, ML classifier",
    exclusion_criteria="Non-PD populations, no ML methods",
    section_output_formats={
        "Study Design":                  "table",
        "Participants: Disordered Group": "yes_no",
        "Features and Models":           "bullet_list",
        "Data Collection":               "table",
    },
)
result = await pipeline.run(auto_confirm=True)

# Access structured section outputs per study
for rubric in result.data_charting_rubrics:
    for section_title, out in rubric.section_outputs.items():
        print(f"[{rubric.source_id}] {section_title} ({out.format_used})")
        print(out.formatted_answer)
        if out.section_summary:
            print(f"  Summary: {out.section_summary}")
```

**Full config API — custom titles, ordering, and formats:**

```python
from prisma_review_agent.models import ReviewProtocol, RubricSectionConfig

protocol = ReviewProtocol(
    title="Emotion recognition from physiological signals",
    objective="...",
    inclusion_criteria="...",
    exclusion_criteria="...",
    rubric_section_config=[
        RubricSectionConfig(section_key="F", section_name="ML Models & Performance", order=1, output_format="table"),
        RubricSectionConfig(section_key="B", section_name="Study Design",            order=2, output_format="table"),
        RubricSectionConfig(section_key="C", section_name="Patient Cohort",          order=3, output_format="yes_no"),
        RubricSectionConfig(section_key="G", section_name="Key Findings",            order=4, output_format="bullet_list"),
    ],
)
```

**Export per-rubric outputs:**

```python
from prisma_review_agent.export import to_rubric_markdown, to_rubric_json

# Markdown: one heading per study, one sub-heading per section
Path("rubric_extraction.md").write_text(to_rubric_markdown(result))

# JSON: list of {source_id, title, sections: {title: {format_used, formatted_answer, section_summary}}}
Path("rubric_extraction.json").write_text(to_rubric_json(result))
```

The combined per-study outputs are also available on `result.prisma_review.methods.data_extraction` (one `StudyDataExtractionReport` per included study, sections in configured order).

**Validation:** Invalid format values raise `ValueError` at `ReviewProtocol` construction time. Unknown section names in `section_output_formats` log a `UserWarning` and are ignored. If the LLM cannot produce the requested format for a section it falls back to `descriptive` and logs a warning — `formatted_answer` is never empty.

### Field-Level Charting & Appraisal Output

Configure per-field answer constraints (enumerated options, yes/no, free text, numeric) and a structured critical appraisal instrument with domain-level concern aggregation.

**Zero-config — built-in defaults:**

```python
from prisma_review_agent import PRISMAReviewPipeline, ReviewProtocol
from prisma_review_agent.export import to_charting_markdown, to_charting_json, to_appraisal_markdown, to_appraisal_json
from pathlib import Path

protocol = ReviewProtocol(
    title="Bio-acoustic ML in neurological disorders",
    inclusion_criteria="...",
    exclusion_criteria="...",
    # charting_template and critical_appraisal_config default to built-in schemas
)

result = await PRISMAReviewPipeline(api_key="...", protocol=protocol).run(auto_confirm=True)

# Per-study field-level extraction
Path("charting.md").write_text(to_charting_markdown(result))
Path("charting.json").write_text(to_charting_json(result))

# Structured appraisal with cross-study summary
Path("appraisal.md").write_text(to_appraisal_markdown(result))
Path("appraisal.json").write_text(to_appraisal_json(result))
```

Access the structured data directly:

```python
for study in result.prisma_review.methods.data_extraction:
    print(f"\n=== {study.source_id} ===")
    for section_key, section in study.field_answers.items():
        print(f"  {section.section_title}")
        for fa in section.field_answers:
            print(f"    {fa.field_name}: {fa.value} [{fa.confidence}]")

for appraisal in result.prisma_review.methods.critical_appraisal_results:
    print(f"\n=== {appraisal.source_id} ===")
    for domain in appraisal.domains:
        print(f"  {domain.domain_name}: {domain.domain_concern}")
```

**Customise a single field's options:**

```python
from prisma_review_agent.agents import default_charting_template

template = default_charting_template()
custom = template.override_field(
    section_key="B",
    field_name="Study Design",
    options=["Cross-sectional", "Longitudinal", "Retrospective cohort", "Prospective cohort"],
)
protocol = ReviewProtocol(..., charting_template=custom)
```

**Fully custom charting template:**

```python
from prisma_review_agent.models import ChartingTemplate, ChartingSection, FieldDefinition

template = ChartingTemplate(sections=[
    ChartingSection(
        section_key="1",
        section_title="Study Overview",
        fields=[
            FieldDefinition(
                field_name="Design",
                description="Overall study design",
                answer_type="enumerated",
                options=["RCT", "Cohort", "Case-control", "Cross-sectional"],
            ),
            FieldDefinition(field_name="Sample Size", description="Total N", answer_type="numeric"),
            FieldDefinition(field_name="Country", description="Study country", answer_type="free_text"),
        ],
    ),
    ChartingSection(
        section_key="2",
        section_title="Outcomes",
        fields=[
            FieldDefinition(
                field_name="Primary Outcome Reported",
                description="Was the primary outcome clearly reported?",
                answer_type="yes_no_extended",
                options=["Yes", "No", "Not Reported"],
            ),
            FieldDefinition(
                field_name="Key Results",
                description="Headline result",
                answer_type="free_text",
            ),
            FieldDefinition(
                field_name="Reviewer Assessment",
                description="Qualitative assessment — filled by reviewer",
                answer_type="free_text",
                reviewer_only=True,    # excluded from LLM extraction
            ),
        ],
    ),
])
protocol = ReviewProtocol(..., charting_template=template)
```

`reviewer_only=True` fields are excluded from the LLM prompt and rendered as `[Human reviewer]` in Markdown exports and `{"value": null, "reviewer_only": true}` in JSON exports.

**Custom critical appraisal instrument:**

```python
from prisma_review_agent.models import CriticalAppraisalConfig, AppraisalDomainSpec, AppraisalItemSpec

config = CriticalAppraisalConfig(domains=[
    AppraisalDomainSpec(
        domain_name="Reporting Quality",
        concern_aggregation_rule="majority_yes",   # or "strict" / "lenient"
        items=[
            AppraisalItemSpec(
                item_text="Were CONSORT/STROBE reporting guidelines followed?",
                allowed_ratings=["Yes", "Partial", "No", "Not Reported"],
            ),
            AppraisalItemSpec(
                item_text="Was the primary outcome pre-registered?",
                allowed_ratings=["Yes", "No", "N/A"],
            ),
        ],
    ),
])
protocol = ReviewProtocol(..., critical_appraisal_config=config)
```

`domain_concern` (`Low` / `Some` / `High`) is derived deterministically in Python from item ratings — it is never left to the LLM. The three aggregation rules:

| Rule | Low | Some | High |
|------|-----|------|------|
| `majority_yes` | > 50% Yes | mixed | > 50% No / Not Reported |
| `strict` | all Yes | any Partial or one No | two or more No |
| `lenient` | any Yes | all Partial / mixed | all No / Not Reported |

**Save and reload a template:**

```python
from pathlib import Path
from prisma_review_agent.models import ChartingTemplate
from prisma_review_agent.agents import default_charting_template

template = default_charting_template()
Path("my_template.json").write_text(template.model_dump_json(indent=2))

loaded = ChartingTemplate.model_validate_json(Path("my_template.json").read_text())
assert loaded == template   # full round-trip fidelity
```

**`confirm_callback` return value semantics:**

| Return value | Meaning | Pipeline action |
|---|---|---|
| `True` | Plan approved | Continue to article retrieval |
| `False` | Plan rejected | Raise `PlanRejectedError` |
| `""` (empty string) | Treated as approval | Continue to article retrieval |
| `"<feedback text>"` | Re-generate with feedback | Call agent again with feedback; increment iteration |

### FastAPI Integration

The pipeline's `confirm_callback` and `progress_callback` hooks make it straightforward to build a live UI on top of FastAPI. Progress messages now carry structured per-article information (stage name, done/total/remaining counts) that the server parses into typed SSE events so the UI can render live progress bars, article cards, and stage indicators without any client-side string parsing.

#### Shared helpers (used by all patterns)

Put these in a shared module (e.g. `shared.py`) imported by every pattern below.

```python
# shared.py
import os
import re
import json
from pydantic import BaseModel as PydanticBase, Field

# ── concurrency default ───────────────────────────────────────────────────────

def _default_concurrency() -> int:
    """2× logical CPUs, clamped to [8, 10]. Falls back to 8 in restricted containers."""
    try:
        return max(8, min((os.cpu_count() or 4) * 2, 10))
    except Exception:
        return 8


# ── request model ─────────────────────────────────────────────────────────────

class ReviewRequest(PydanticBase):
    title: str
    inclusion: str = ""
    exclusion: str = ""
    assemble_timeout: float = 3600.0
    concurrency: int = Field(
        default_factory=_default_concurrency,
        ge=1, le=20,
        description="Max concurrent LLM calls per article step. Auto-detected from CPU count (8–10).",
    )
    section_output_formats: dict[str, str] = {}
    rubric_section_config: list[dict] = []


# ── progress message parser ───────────────────────────────────────────────────

# Matches: "✓ 28087124 [5/38 done, 33 remaining]"  (completion line)
_RE_ARTICLE_DONE  = re.compile(r"✓\s+(\S+)\s+\[(\d+)/(\d+) done,\s*(\d+) remaining\]")
# Matches: "Charting [3/38, 35 remaining] 28087124…" (start line)
_RE_ARTICLE_START = re.compile(r"\[(\d+)/(\d+),\s*(\d+) remaining\]\s+(\S+)")
# Matches: "[1/38] Some title…"  (extraction / RoB start line)
_RE_IDX_TITLE     = re.compile(r"\[(\d+)/(\d+)\]\s+(.+)")

# Stage keywords → canonical stage name
_STAGE_KEYWORDS = {
    "Screening": "screening",
    "Extracting evidence": "evidence_extraction",
    "Extracting data from": "data_extraction",
    "Assessing risk of bias": "risk_of_bias",
    "Charting": "data_charting",
    "Appraising": "critical_appraisal",
    "Narrative": "narrative_synthesis",
    "Synthesizing": "synthesis",
    "Assessing bias": "bias_assessment",
    "GRADE": "grade",
}


def parse_progress_message(msg: str, session: dict) -> dict:
    """Convert a raw pipeline progress string into a typed event dict.

    Updates session["stage"], session["stage_done"], session["stage_total"],
    and session["stage_remaining"] in place so /progress can serve a snapshot.

    Returned dict always has a "type" key. Types:
      log           — generic informational line
      stage_start   — a new pipeline stage has begun
      article_start — a single article started processing (non-blocking)
      article_done  — a single article finished; includes done/total/remaining
      stage_done    — all articles in the current stage finished (remaining == 0)
    """
    stripped = msg.strip()

    # ── article completion line ────────────────────────────────────────────────
    m = _RE_ARTICLE_DONE.search(stripped)
    if m:
        pmid, done, total, remaining = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
        session["stage_done"]      = done
        session["stage_total"]     = total
        session["stage_remaining"] = remaining
        event = {
            "type":      "article_done",
            "pmid":      pmid,
            "done":      done,
            "total":     total,
            "remaining": remaining,
            "stage":     session.get("stage", ""),
            "message":   stripped,
        }
        if remaining == 0:
            event["type"] = "stage_done"
        return event

    # ── article start line (Charting / Appraising / Narrative) ────────────────
    m = _RE_ARTICLE_START.search(stripped)
    if m:
        idx, total, remaining, pmid = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
        session["stage_total"]     = total
        session["stage_remaining"] = remaining
        return {
            "type":      "article_start",
            "pmid":      pmid,
            "index":     idx,
            "total":     total,
            "remaining": remaining,
            "stage":     session.get("stage", ""),
            "message":   stripped,
        }

    # ── extraction / RoB start line "[1/38] Title…" ───────────────────────────
    m = _RE_IDX_TITLE.search(stripped)
    if m:
        idx, total, title = int(m.group(1)), int(m.group(2)), m.group(3)
        session["stage_total"] = total
        return {
            "type":    "article_start",
            "index":   idx,
            "total":   total,
            "title":   title[:80],
            "stage":   session.get("stage", ""),
            "message": stripped,
        }

    # ── stage start line ──────────────────────────────────────────────────────
    for keyword, stage_name in _STAGE_KEYWORDS.items():
        if keyword in stripped:
            session["stage"]           = stage_name
            session["stage_done"]      = 0
            session["stage_remaining"] = 0
            # extract total if present: "Extracting data from 38 studies"
            m_total = re.search(r"(\d+)\s+(?:studies|articles)", stripped)
            if m_total:
                session["stage_total"] = int(m_total.group(1))
            return {
                "type":    "stage_start",
                "stage":   stage_name,
                "total":   session.get("stage_total", 0),
                "message": stripped,
            }

    # ── generic log line ──────────────────────────────────────────────────────
    return {"type": "log", "message": stripped}
```

#### Pattern 1 — Full session with plan confirmation, structured progress, and SSE

This is the recommended pattern for a production UI. It exposes:
- `POST /review/start` — start a review, get a `session_id`
- `GET  /review/{id}/stream` — SSE stream of typed events
- `GET  /review/{id}/progress` — polling snapshot (alternative to SSE)
- `GET  /review/{id}/plan` — retrieve the generated search plan
- `POST /review/confirm` — approve / reject / give feedback on the plan
- `GET  /review/{id}/status` — final result once complete

```python
import asyncio
import json
import uuid
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel as PydanticBase
from prisma_review_agent.models import (
    ReviewPlan, ReviewProtocol, PlanRejectedError, MaxIterationsReachedError,
    RubricSectionConfig,
)
from prisma_review_agent.pipeline import PRISMAReviewPipeline
from shared import ReviewRequest, _default_concurrency, parse_progress_message

app = FastAPI()
_sessions: dict[str, dict] = {}


def _new_session() -> dict:
    return {
        "status":          "starting",   # starting | running | awaiting_confirmation
                                          # | complete | rejected | error | timeout
        "stage":           None,          # current pipeline stage name
        "stage_total":     0,             # articles in current stage
        "stage_done":      0,             # articles completed in current stage
        "stage_remaining": 0,             # articles still pending in current stage
        "articles_included": 0,           # running count of included articles
        "plan":            None,          # ReviewPlan dict (set when awaiting confirmation)
        "events":          [],            # structured event dicts (consumed by SSE)
        "log":             [],            # raw progress strings (full audit trail)
        "result":          None,          # PRISMAReviewResult dict once complete
        "error":           None,
        # internal only — not serialised to clients
        "_confirm_event":  asyncio.Event(),
        "_confirm_response": None,
    }


class ConfirmRequest(PydanticBase):
    session_id: str
    response: str   # "yes" | "no" | feedback text


# ── helpers ───────────────────────────────────────────────────────────────────

def _append_event(session: dict, msg: str) -> None:
    """Parse a progress message, update session state, and append a typed event."""
    session["log"].append(msg)
    event = parse_progress_message(msg, session)
    session["events"].append(event)

    # track running included count from flow summary lines
    m_inc = __import__("re").search(r"Final included:\s*(\d+)", msg)
    if m_inc:
        session["articles_included"] = int(m_inc.group(1))


def _public_session(session: dict) -> dict:
    """Session fields safe to return to the client (no internal asyncio objects)."""
    return {
        "status":            session["status"],
        "stage":             session["stage"],
        "stage_total":       session["stage_total"],
        "stage_done":        session["stage_done"],
        "stage_remaining":   session["stage_remaining"],
        "articles_included": session["articles_included"],
        "plan":              session["plan"],
        "result":            session["result"],
        "error":             session["error"],
    }


# ── endpoints ─────────────────────────────────────────────────────────────────

@app.post("/review/start")
async def start_review(req: ReviewRequest):
    session_id = str(uuid.uuid4())
    session = _new_session()
    _sessions[session_id] = session

    concurrency = min(req.concurrency, 10)   # server-side hard cap
    timeout     = min(req.assemble_timeout, 7200.0)

    rubric_cfg = [RubricSectionConfig(**c) for c in req.rubric_section_config]
    protocol = ReviewProtocol(
        title=req.title,
        inclusion_criteria=req.inclusion,
        exclusion_criteria=req.exclusion,
        section_output_formats=req.section_output_formats,
        rubric_section_config=rubric_cfg,
        article_concurrency=concurrency,
    )
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        model_name="anthropic/claude-sonnet-4",
        protocol=protocol,
    )

    def confirm_callback(plan: ReviewPlan) -> bool | str:
        session["plan"]   = plan.model_dump()
        session["status"] = "awaiting_confirmation"
        session["_confirm_event"].clear()
        _append_event(session, f"Plan ready — iteration {plan.iteration}")
        asyncio.get_event_loop().run_until_complete(session["_confirm_event"].wait())
        return session["_confirm_response"]

    def progress_callback(msg: str) -> None:
        session["status"] = "running"
        _append_event(session, msg)

    asyncio.create_task(_run(pipeline, session, confirm_callback, progress_callback, timeout))
    return {"session_id": session_id, "concurrency": concurrency}


async def _run(pipeline, session, confirm_cb, progress_cb, timeout: float):
    try:
        result = await pipeline.run(
            confirm_callback=confirm_cb,
            progress_callback=progress_cb,
            assemble_timeout=timeout,
        )
        session["result"] = result.model_dump(mode="json")
        session["status"] = "complete"
        session["events"].append({"type": "done", "status": "complete"})
    except PlanRejectedError:
        session["status"] = "rejected"
        session["events"].append({"type": "done", "status": "rejected"})
    except asyncio.TimeoutError:
        session["status"] = "timeout"
        session["error"]  = f"Assembly exceeded {timeout:.0f}s"
        session["events"].append({"type": "done", "status": "timeout"})
    except MaxIterationsReachedError as e:
        session["status"] = f"max_iterations"
        session["error"]  = str(e)
        session["events"].append({"type": "done", "status": "max_iterations"})
    except Exception as e:
        session["status"] = "error"
        session["error"]  = str(e)
        session["events"].append({"type": "done", "status": "error", "detail": str(e)})


@app.get("/review/{session_id}/stream")
async def stream_events(session_id: str):
    """SSE stream of typed events. Each event has a named type and a JSON data payload.

    Event types emitted:
      log            — generic informational line        {"message": "..."}
      stage_start    — new pipeline stage began          {"stage": "data_charting", "total": 38, ...}
      article_start  — single article started            {"pmid": "...", "index": 3, "total": 38, "remaining": 35, ...}
      article_done   — single article finished           {"pmid": "...", "done": 3, "total": 38, "remaining": 35, ...}
      stage_done     — all articles in stage finished    {"stage": "data_charting", "done": 38, ...}
      plan_ready     — search plan awaits confirmation   {"plan": {...}}
      done           — pipeline finished (any outcome)   {"status": "complete"|"error"|"rejected"|...}
    """
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")

    last_idx = 0

    async def generator():
        nonlocal last_idx
        while True:
            events = session["events"]
            while last_idx < len(events):
                ev = events[last_idx]
                last_idx += 1
                ev_type = ev.get("type", "log")
                # emit plan_ready as a named SSE event so the browser can
                # listen with addEventListener("plan_ready", ...)
                if ev_type == "log" and session["status"] == "awaiting_confirmation":
                    yield f"event: plan_ready\ndata: {json.dumps({'plan': session['plan']})}\n\n"
                else:
                    yield f"event: {ev_type}\ndata: {json.dumps(ev)}\n\n"
                if ev_type == "done":
                    return
            await asyncio.sleep(0.2)

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/review/{session_id}/progress")
async def get_progress(session_id: str):
    """Polling alternative to SSE — returns current stage snapshot and recent log lines."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    snap = _public_session(session)
    snap["recent_log"] = session["log"][-20:]   # last 20 raw lines for debugging
    return snap


@app.get("/review/{session_id}/plan")
async def get_plan(session_id: str):
    """Returns the generated search plan when status == 'awaiting_confirmation'."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session["status"] == "awaiting_confirmation" and session["plan"]:
        return {"status": "awaiting_confirmation", "plan": session["plan"]}
    return {"status": session["status"]}


@app.post("/review/confirm")
async def confirm_plan(req: ConfirmRequest):
    """Approve, reject, or give feedback on the search plan."""
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    r = req.response.strip()
    if r.lower() in ("yes", "y", ""):
        session["_confirm_response"] = True
    elif r.lower() in ("no", "abort"):
        session["_confirm_response"] = False
    else:
        session["_confirm_response"] = r   # feedback → plan re-generation
    session["_confirm_event"].set()
    return {"status": "acknowledged"}


@app.get("/review/{session_id}/status")
async def get_status(session_id: str):
    """Returns the full result once status == 'complete'."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session["status"] == "timeout":
        raise HTTPException(504, session.get("error", "Assembly timed out"))
    return _public_session(session)
```

#### Pattern 2 — JavaScript / TypeScript client

Consume the SSE stream and drive a live progress UI. Paste this into any framework (React, Vue, plain JS).

```typescript
// reviewStream.ts
export type EventType =
  | "log" | "stage_start" | "article_start" | "article_done"
  | "stage_done" | "plan_ready" | "done";

export interface ProgressEvent {
  type: EventType;
  message?: string;
  stage?: string;
  pmid?: string;
  index?: number;
  done?: number;
  total?: number;
  remaining?: number;
  plan?: Record<string, unknown>;
  status?: string;
  detail?: string;
}

export interface ProgressState {
  status: string;
  stage: string | null;
  stageTotal: number;
  stageDone: number;
  stageRemaining: number;
  articlesIncluded: number;
  log: string[];
}

export function connectReviewStream(
  sessionId: string,
  onEvent: (ev: ProgressEvent, state: ProgressState) => void,
  onDone: (status: string) => void,
): () => void {
  const state: ProgressState = {
    status: "running",
    stage: null,
    stageTotal: 0,
    stageDone: 0,
    stageRemaining: 0,
    articlesIncluded: 0,
    log: [],
  };

  const es = new EventSource(`/review/${sessionId}/stream`);

  const handle = (type: EventType) => (raw: MessageEvent) => {
    const ev: ProgressEvent = { ...JSON.parse(raw.data), type };

    // keep local state in sync
    if (type === "stage_start") {
      state.stage         = ev.stage ?? state.stage;
      state.stageTotal    = ev.total ?? 0;
      state.stageDone     = 0;
      state.stageRemaining = ev.total ?? 0;
    }
    if (type === "article_done" || type === "stage_done") {
      state.stageDone      = ev.done      ?? state.stageDone;
      state.stageRemaining = ev.remaining ?? 0;
    }
    if (type === "log" && ev.message) {
      state.log.push(ev.message);
      if (state.log.length > 200) state.log.shift();
      // parse "Final included: N" from log
      const m = ev.message.match(/Final included:\s*(\d+)/);
      if (m) state.articlesIncluded = parseInt(m[1], 10);
    }
    if (type === "done") {
      state.status = ev.status ?? "done";
      es.close();
      onDone(state.status);
      return;
    }

    onEvent(ev, { ...state });
  };

  // one listener per event type
  (["log","stage_start","article_start","article_done","stage_done","plan_ready","done"] as EventType[])
    .forEach(t => es.addEventListener(t, handle(t) as EventListener));

  es.onerror = () => {
    state.status = "error";
    es.close();
    onDone("error");
  };

  return () => es.close();   // call to disconnect early
}
```

**Usage in a React component:**

```tsx
import { useEffect, useState } from "react";
import { connectReviewStream, ProgressState, ProgressEvent } from "./reviewStream";

export function ReviewProgress({ sessionId }: { sessionId: string }) {
  const [state, setState] = useState<ProgressState | null>(null);
  const [events, setEvents] = useState<ProgressEvent[]>([]);

  useEffect(() => {
    const disconnect = connectReviewStream(
      sessionId,
      (ev, snap) => {
        setState({ ...snap });
        setEvents(prev => [...prev.slice(-100), ev]);   // keep last 100
      },
      (finalStatus) => console.log("done:", finalStatus),
    );
    return disconnect;
  }, [sessionId]);

  if (!state) return <p>Connecting…</p>;

  const pct = state.stageTotal > 0
    ? Math.round((state.stageDone / state.stageTotal) * 100)
    : 0;

  return (
    <div>
      <p>Status: {state.status} · Stage: {state.stage ?? "—"}</p>
      <p>Articles included so far: {state.articlesIncluded}</p>

      {/* progress bar */}
      {state.stageTotal > 0 && (
        <div style={{ background: "#eee", borderRadius: 4, height: 8, width: "100%" }}>
          <div style={{ background: "#4f46e5", width: `${pct}%`, height: "100%", borderRadius: 4 }} />
        </div>
      )}
      <p>{state.stageDone}/{state.stageTotal} done · {state.stageRemaining} remaining</p>

      {/* live log */}
      <ul style={{ fontFamily: "monospace", fontSize: 12 }}>
        {state.log.slice(-15).map((line, i) => <li key={i}>{line}</li>)}
      </ul>

      {/* article cards for done events */}
      {events.filter(e => e.type === "article_done").slice(-5).map((e, i) => (
        <div key={i} style={{ border: "1px solid #ccc", padding: 6, marginTop: 4 }}>
          ✓ {e.pmid} &nbsp;
          <span style={{ color: "#888" }}>
            [{e.done}/{e.total}, {e.remaining} remaining]
          </span>
        </div>
      ))}
    </div>
  );
}
```

#### Pattern 3 — Polling fallback (no SSE)

For environments where SSE is unavailable (some proxies, load balancers), poll `/progress` every 2 seconds instead:

```typescript
async function pollProgress(sessionId: string, onUpdate: (snap: object) => void) {
  while (true) {
    const res  = await fetch(`/review/${sessionId}/progress`);
    const snap = await res.json();
    onUpdate(snap);
    if (["complete","error","rejected","timeout"].includes(snap.status)) break;
    await new Promise(r => setTimeout(r, 2000));
  }
}
```

`/progress` returns:

```json
{
  "status":            "running",
  "stage":             "data_charting",
  "stage_total":       38,
  "stage_done":        12,
  "stage_remaining":   26,
  "articles_included": 38,
  "plan":              null,
  "result":            null,
  "error":             null,
  "recent_log": [
    "  Charting [11/38, 27 remaining] 28087124…",
    "  ✓ Charted 28087124 [12/38 done, 26 remaining]",
    "  Charting [13/38, 25 remaining] 36175756…"
  ]
}
```

**Key points:**
- `parse_progress_message` runs server-side — the UI receives clean typed events and never parses strings.
- `stage_remaining` counts down to 0 as parallel article tasks complete; the UI can use it to drive a live countdown or progress bar for each stage.
- `X-Accel-Buffering: no` on the SSE response header is required when running behind nginx so it does not buffer the stream.
- The in-memory `_sessions` dict works for single-process development. In production use Redis pub/sub for SSE fan-out and a persistent store for session state.
- `asyncio.get_event_loop().run_until_complete(event.wait())` in `confirm_callback` works in a single-threaded asyncio loop; if the pipeline runs in a thread pool, use `loop.call_soon_threadsafe(event.set)` instead.
- Server-side cap `min(req.concurrency, 10)` prevents an untrusted caller from flooding the LLM API.
- `_default_concurrency()` uses `os.cpu_count() * 2`, clamped to 8–10, with a hard fallback of 8 when `cpu_count()` returns `None` (common in Docker with restricted cgroups).

#### Suggested UI for the Plan Confirmation Phase

Inspired by research review tools (see design reference in the project), the plan confirmation screen should feel like a structured "contract" the user approves before the pipeline does any expensive work. Suggested layout (following the KSynth-style design):

```mermaid
flowchart TB
    subgraph Screen["Plan Confirmation — Protocol Tab"]
        direction TB
        Nav["Protocol ← selected  ·  Progress  ·  Synthesis  ·  PRISMA Flow  ·  Export"]

        subgraph Plan["Generated Search Plan — Iteration 1"]
            direction TB
            RQ["Research Question\nCRISPR gene therapy efficacy in clinical trials"]

            subgraph QBox["Queries — editable before approval"]
                direction LR
                PQ["PubMed × 3\n1. CRISPR gene therapy clinical trials\n2. CRISPR-Cas9 human trials outcomes\n3. gene editing therapy safety RCT"]
                BQ["bioRxiv × 2\n1. CRISPR Cas9 gene editing safety\n2. CRISPR therapy clinical outcomes preprint"]
            end

            MeSH["MeSH pills · CRISPR-Cas Systems · Gene Therapy · Clinical Trials as RCTs"]
            KC["Key concepts · efficacy · safety · clinical trial · gene editing"]
            RT["Rationale: Focused on clinical evidence matching inclusion criteria..."]
            FB["Feedback optional: Add pediatric studies, extend date range..."]
        end

        subgraph Actions["Actions"]
            direction LR
            B1["✗ Reject"] --- B2["↻ Regenerate"] --- B3["✓ Approve →"]
        end
    end

    Nav --> Plan --> Actions
    RQ --> QBox --> MeSH --> KC --> RT --> FB
```

**Key UX decisions:**
- **Plan appears inline** in the "Progress" tab (not a modal) — so the user can scroll up to review the protocol they entered before approving
- **Queries are editable** before approval — send edited queries back as feedback text via `confirm_callback`
- **MeSH terms and key concepts** render as pill badges (matching the "Charting Questions" style from the screenshot)
- **Feedback textarea** is pre-populated with `""` and only sent if non-empty; empty submit = `"yes"`
- **Reject** posts `response: "no"` and redirects to the project list
- **Regenerate** posts the feedback text; the plan card replaces itself with the new iteration
- **Approve** posts `response: "yes"` and transitions the Progress tab to the live SSE log view

**Progress tab after approval (SSE stream view):**

```mermaid
flowchart TB
    subgraph Screen["Progress Tab — Live SSE View"]
        direction TB
        Hdr["Running · 0 included so far                              Cancel"]

        subgraph Log["Pipeline Log"]
            direction TB
            S1["✓  Plan approved — Iteration 1"]
            S2["✓  Searching PubMed — 3 queries sent"]
            S3["✓  47 records retrieved"]
            S4["✓  Deduplication — 6 duplicates removed"]
            S5["⟳  Screening title/abstract — 41 records  in progress"]

            subgraph Cards["Per-article decisions  streamed live"]
                direction LR
                C1["PMID 33283989\n✓ Include\nCRISPR-Cas9 for SCD trial"]
                C2["PMID 38661449\n✓ Include\nExagamglogene Autotemcel"]
                C3["PMID 29301234\n✗ Exclude\nAnimal model only"]
            end
        end
    end

    Hdr --> Log
    S1 --> S2 --> S3 --> S4 --> S5 --> Cards
```

This mirrors the "Running · 0 included" sidebar state in the KSynth screenshot and the evidence card grid in the Evidence tab.

## Enhanced Output Formats

The PRISMA Agent now includes comprehensive structured outputs for systematic review documentation:

### Data Charting Rubric (CSV)
Structured extraction of study characteristics across 7 sections (A-G):
- **Section A**: Publication Information (title, authors, year, journal, DOI, database)
- **Section B**: Study Design (goals, design type, sample size, tasks, settings)
- **Section C**: Disordered Group Participants (diagnosis, assessment, demographics)
- **Section D**: Healthy Controls (inclusion, matching criteria)
- **Section E**: Data Collection (data types, tasks, equipment, datasets)
- **Section F**: Features & Models (feature types, algorithms, performance metrics)
- **Section G**: Synthesis (key findings, limitations, future directions)

### PRISMA Narrative Rows (CSV)
Condensed 6-cell summary format derived from charting data:
- Study design/sample/dataset
- Methods (feature extraction, modeling, validation)
- Outcomes (key performance results + findings)
- Key limitations
- Relevance notes
- Review-specific questions

### Critical Appraisal Rubric (CSV)
Quality assessment across 4 domains:
- **Domain 1**: Participant & Sample Quality (5 items)
- **Domain 2**: Data Collection Quality (3 items)
- **Domain 3**: Feature & Model Quality (5 items)
- **Domain 4**: Bias & Transparency (4 items)

Each domain includes item-level ratings (Yes/Partial/No/Not Reported/N/A) and overall concern (Low/Some/High).

### Enhanced Markdown
Professional systematic literature review brief with HTML styling, figures, and comprehensive documentation including:
- **Executive Summary** with key findings and statistics
- **Background & Rationale** with PICO framework
- **Detailed Methods** with eligibility criteria tables and search strategies
- **Comprehensive Results** with PRISMA flow diagrams, study characteristics, and visual data representations
- **Discussion** with implications for practice and research
- **Conclusions** with key takeaways
- **References** in academic format
- **Detailed Appendices** with data charting rubrics, critical appraisal results, and evidence spans

The enhanced format produces publication-ready SLR briefs with professional styling, color-coded sections, and visual elements suitable for stakeholder presentations and academic publications.

### Export Options

```bash
# Default enhanced format
prisma-review --title "..." --export enhanced_md

# All structured formats
prisma-review --title "..." --export enhanced_md charting_csv narrative_csv appraisal_csv

# Individual formats
prisma-review --title "..." --export charting narrative appraisal json

# Compare-mode exports (after running with --compare-models)
prisma-review --title "..." --compare-models anthropic/claude-sonnet-4 openai/gpt-4o \
  --auto --export md json

# RDF / Linked Data formats
prisma-review --title "..." --export ttl           # Turtle RDF
prisma-review --title "..." --export jsonld        # JSON-LD
prisma-review --title "..." --export ttl jsonld md # all three together

# Persist a queryable pyoxigraph store
prisma-review --title "..." --export ttl --rdf-store-path review.ttl
```

### RDF / Linked Data Export

Export results as RDF using the [SLR Ontology](https://w3id.org/slr-ontology/) (v0.2.0). The Turtle and JSON-LD files are self-contained linked-data documents that can be loaded into any SPARQL endpoint (Apache Jena, Oxigraph, Blazegraph, etc.) or processed with standard RDF tools.

**Namespace prefixes used:**

| Prefix | URI |
|--------|-----|
| `slr:` | `https://w3id.org/slr-ontology/` |
| `prov:` | `http://www.w3.org/ns/prov#` |
| `dcterms:` | `http://purl.org/dc/terms/` |
| `fabio:` | `http://purl.org/spar/fabio/` |
| `bibo:` | `http://purl.org/ontology/bibo/` |
| `oa:` | `http://www.w3.org/ns/oa#` |
| `xsd:` | `http://www.w3.org/2001/XMLSchema#` |

**Python API:**

```python
from prisma_review_agent.export import to_turtle, to_jsonld

turtle_str = to_turtle(result)
jsonld_str = to_jsonld(result)
```

### Pyoxigraph SPARQL Store

For in-process SPARQL queries, load the result directly into a [pyoxigraph](https://pyoxigraph.readthedocs.io/) store:

```python
from prisma_review_agent.export import to_oxigraph_store

store = to_oxigraph_store(result)

# Find all included sources
rows = store.query("""
    PREFIX slr: <https://w3id.org/slr-ontology/>
    PREFIX dcterms: <http://purl.org/dc/terms/>
    SELECT ?src ?title WHERE {
        ?src a slr:IncludedSource ;
             dcterms:title ?title .
    }
""")

# Check provenance timestamp
rows = store.query("""
    PREFIX prov: <http://www.w3.org/ns/prov#>
    SELECT ?review ?t WHERE { ?review prov:generatedAtTime ?t }
""")

# Save store to disk for later re-use
store.save("review_store.ttl")
```

Or from the CLI — pass `--rdf-store-path` to write the store after export:

```bash
prisma-review --title "..." --export ttl --rdf-store-path review_store.ttl
```

**Note**: The system processes ALL studies that pass screening criteria through complete data charting and critical appraisal. There are no artificial limits on corpus size — from small pilot reviews (5-10 studies) to comprehensive systematic reviews (50+ studies).

## Performance

Steps 7–15 are fully parallelised with `asyncio` in **both** the standard pipeline and compare mode. Multiple articles are processed concurrently, bounded by a semaphore so you never exceed your LLM provider's rate limit. T/A screening (step 7) and FT screening (step 9) share the same helper functions across both execution paths, so tuning `--concurrency` applies uniformly everywhere.

In compare mode, per-model pipelines run concurrently with each other *and* each pipeline internally parallelises all article-level steps — so the combined speedup compounds.

### Expected speedup

| Articles included | Sequential | Concurrency 5 | Concurrency 10 |
|:-----------------:|:----------:|:-------------:|:--------------:|
| 38 | ~70 min | ~15 min | ~8 min |
| 100 | ~3 h | ~40 min | ~20 min |
| 1 000 + 10 citation hops | ~15 h | ~3 h | ~1.5 h |

*Times are approximate and depend on model latency and API rate limits. Compare-mode runs see an additional multiplier because each model pipeline is itself fully parallel.*

### Tuning concurrency

**CLI:**

```bash
# Moderate parallelism (default) — safe for most OpenRouter tiers
prisma-review --title "..." --concurrency 5

# High parallelism — use if your API tier supports it
prisma-review --title "..." --concurrency 10

# Sequential (debugging / strict rate-limit compliance)
prisma-review --title "..." --concurrency 1
```

**Python API:**

```python
protocol = ReviewProtocol(
    title="...",
    inclusion_criteria="...",
    exclusion_criteria="...",
    article_concurrency=10,   # 1–20; default 5
)
```

**Guidance:**
- **Default (5)** — good starting point; respects most provider rate limits.
- **10** — recommended for large reviews (100+ included articles) when your OpenRouter tier allows higher throughput.
- **1** — fully sequential; useful for debugging or very strict rate-limit environments.
- Values above 10 rarely improve wall-clock time because the bottleneck shifts to LLM latency rather than throughput.

## Pipeline Steps (17-step Enhanced PRISMA)

| Step | Agent | Output Type | Description |
|------|-------|-------------|-------------|
| 1. Search Strategy | `search_strategy_agent` | `SearchStrategy` | Generates PubMed + bioRxiv queries from protocol |
| 2. PubMed Search | — (HTTP) | `list[Article]` | E-utilities esearch + efetch |
| 3. bioRxiv Search | — (HTTP) | `list[Article]` | bioRxiv API keyword matching |
| 4. Related Articles | — (HTTP) | `list[str]` | elink neighbor_score |
| 5. Citation Hops | — (HTTP) | `list[Article]` | Forward (cited-by) + backward navigation |
| 6. Deduplication | — (logic) | `list[Article]` | DOI/PMID dedup |
| 7. Title/Abstract Screening ⚡ | `screening_agent` | `ScreeningBatchResult` | LLM batch screening (inclusive) — parallel batches of 15 |
| 8. Full-text Retrieval | — (HTTP) | `dict[str, str]` | PMC efetch |
| 9. Full-text Screening ⚡ | `screening_agent` | `ScreeningBatchResult` | LLM batch screening (strict) — parallel batches of 10 |
| 10. Evidence Extraction ⚡ | `evidence_extraction_agent` | `BatchEvidenceExtraction` | LLM identifies claims + evidence spans — parallel batches of 5 |
| 11. Data Extraction ⚡ | `data_extraction_agent` | `StudyDataExtraction` | Per-study structured data — fully parallel |
| 12. Risk of Bias ⚡ | `rob_agent` | `RiskOfBiasResult` | Per-study RoB 2 / ROBINS-I / NOS — fully parallel |
| 13. Data Charting ⚡ | `data_charting_agent` | `DataChartingRubric` | Structured charting across 7 sections (A-G) — fully parallel |
| 14. Critical Appraisal ⚡ | `critical_appraisal_agent` | `CriticalAppraisalRubric` | Quality assessment across 4 domains — fully parallel |
| 15. Narrative Rows ⚡ | `narrative_row_agent` | `PRISMANarrativeRow` | Condensed 6-cell summary format — fully parallel |
| 16. Synthesis | `synthesis_agent` | `str` | Grounded narrative with PMID citations |
| 17. Bias + GRADE | `bias_summary_agent` + `grade_agent` | `str` + `GRADEAssessment` | Parallel assessment |
| 18. Limitations | `limitations_agent` | `str` | Review limitations section |

*⚡ = runs with `asyncio` concurrency bounded by `article_concurrency` (default 5, set via `--concurrency`). Steps 7 and 9 use shared helpers `_parallel_ta_screening` / `_parallel_ft_screening` reused by both the standard pipeline and compare mode.*

## Agents Reference

### Agent Architecture

Each agent is defined as a module-level `pydantic_ai.Agent` with:
- **Typed output**: Pydantic model that the LLM must conform to
- **System prompt**: Static instructions + dynamic context from `RunContext[AgentDeps]`
- **Deferred model**: `defer_model_check=True` — model is provided at runtime via `build_model()`
- **Dependencies**: `AgentDeps` dataclass carrying protocol + API credentials

```python
from agents import AgentDeps, build_model, rob_agent
from models import ReviewProtocol

deps = AgentDeps(
    protocol=ReviewProtocol(title="..."),
    api_key="sk-or-v1-...",
    model_name="anthropic/claude-sonnet-4",
)
model = build_model(deps.api_key, deps.model_name)

# Run directly
result = await rob_agent.run(
    "Title: ...\nAbstract: ...",
    deps=deps,
    model=model,
)
rob: RiskOfBiasResult = result.output
print(rob.overall)  # RoBJudgment.LOW
```

### Selecting a Model

Pass any [OpenRouter model ID](https://openrouter.ai/models) via `--model` on the CLI or the `model_name` argument in Python.

**CLI**
```bash
# Claude Sonnet 4 (default)
prisma-review --title "..." --model anthropic/claude-sonnet-4

# Gemini 2.5 Pro
prisma-review --title "..." --model google/gemini-2.5-pro

# GPT-4o
prisma-review --title "..." --model openai/gpt-4o

# DeepSeek (cost-effective)
prisma-review --title "..." --model deepseek/deepseek-chat
```

**Python API**
```python
pipeline = PRISMAReviewPipeline(
    api_key="sk-or-v1-...",
    model_name="google/gemini-2.5-pro",   # ← change here
    protocol=protocol,
)
```

**Interactive mode** — prompts you to type a model name at startup:
```bash
prisma-review --interactive
# Enter model ID when prompted, or press Enter for the default
```

### Supported Models (via OpenRouter)

Any model available on OpenRouter works. Tested with:

| Model | ID | Notes |
|-------|-----|-------|
| Claude Sonnet 4 | `anthropic/claude-sonnet-4` | Best balance of quality/speed |
| Claude Haiku 4 | `anthropic/claude-haiku-4` | Faster, good for screening |
| Gemini 2.5 Pro | `google/gemini-2.5-pro` | Good structured output |
| Gemini 2.5 Flash | `google/gemini-2.5-flash` | Fast; uses text fallback for charting/appraisal |
| GPT-4o | `openai/gpt-4o` | Strong general performance |
| DeepSeek Chat | `deepseek/deepseek-chat` | Cost-effective |
| Llama 3.1 70B | `meta-llama/llama-3.1-70b-instruct` | Open-source option |

**Schema compatibility note:** Google Gemini models reject structured-output schemas with many optional properties (HTTP 400 "too much branching"). The charting and critical appraisal steps automatically detect this error and retry in text mode — the model returns JSON as plain text, which is then parsed into the same data model. All other pipeline steps are unaffected. No configuration is needed; the fallback is transparent.

## Data Models

### Core Models

| Model | Purpose |
|-------|---------|
| `Article` | Research article with metadata, full text, RoB, extracted data |
| `EvidenceSpan` | Single evidence sentence with source, claim label, relevance score |
| `ReviewProtocol` | Full PRISMA protocol: PICO, criteria, databases, registration |
| `PRISMAFlowCounts` | PRISMA flow diagram counts for all stages |
| `PRISMAReviewResult` | Complete review result with all outputs |

### LLM Output Models

| Model | Used By | Description |
|-------|---------|-------------|
| `SearchStrategy` | search_strategy_agent | PubMed/bioRxiv queries, MeSH terms |
| `ScreeningBatchResult` | screening_agent | Batch of include/exclude decisions |
| `RiskOfBiasResult` | rob_agent | Per-domain RoB with overall judgment |
| `StudyDataExtraction` | data_extraction_agent | Study design, findings, effect measures |
| `GRADEAssessment` | grade_agent | GRADE domains + overall certainty |
| `BatchEvidenceExtraction` | evidence_extraction_agent | Evidence spans per article |

## Export Formats

### Markdown
Full PRISMA 2020 structured report with:
- Abstract, Introduction (rationale + PICO), Methods (criteria, search strategy, selection, RoB)
- Results (flow table, study characteristics, synthesis, RoB, GRADE)
- Discussion (limitations), Other Information (registration, funding)
- References, Appendix (evidence spans)

### JSON
Complete `PRISMAReviewResult` serialized via `model_dump_json()`.

### BibTeX
Standard `@article{}` entries for all included studies.

## Caching

### HTTP Cache (SQLite)

SQLite cache (`prisma_agent_cache.db`) stores raw HTTP responses with a 72-hour TTL:
- PubMed search results
- Article metadata and full text
- Related article links
- bioRxiv search results

Disable with `--no-cache` or `enable_cache=False`.

### Review Result Cache (PostgreSQL)

When `--pg-dsn` is provided, completed review results are cached in PostgreSQL. On subsequent runs with ≥ 95% similar criteria (configurable), the full result is served from cache in seconds rather than minutes.

```bash
# Run with PostgreSQL cache
prisma-review \
  --title "GLP-1 agonists for type 2 diabetes" \
  --inclusion "RCTs, English, 2019-2024" \
  --pg-dsn "postgresql://user:pass@localhost/prisma_db" \
  --cache-threshold 0.95 \
  --export md

# Force a fresh run (bypass cache)
prisma-review --title "..." --pg-dsn "..." --force-refresh
```

**Setup** — run the migration once before first use:

```bash
psql "$PRISMA_PG_DSN" -f prisma_review_agent/cache/migrations/001_initial.sql
```

Or set the DSN via environment variable:
```bash
export PRISMA_PG_DSN="postgresql://user:pass@localhost/prisma_db"
prisma-review --title "..."
```

The Markdown export includes a cache banner when a result is served from cache:

```
⚡ Served from cache (similarity 97.3%) — matched: *GLP-1 agonists for type 2 diabetes*
```

### Article Store (PostgreSQL)

All fetched articles are persisted to the `article_store` table (same PostgreSQL connection). Full-text content is indexed with a GIN/tsvector index for fast retrieval. On subsequent runs, stored full text is used as the primary source before falling back to live PubMed fetch — reducing API calls and improving reproducibility.

### Iterative Large-Review Processing (PostgreSQL)

For reviews with hundreds of included articles, the pipeline automatically processes each stage in batches and checkpoints results to a `pipeline_checkpoints` table after every batch. If the process crashes or times out, re-running with the same `review_id` resumes from the last completed batch rather than restarting from scratch.

**Setup** — run the migration once:

```bash
psql "$PRISMA_PG_DSN" -f prisma_review_agent/cache/migrations/003_add_pipeline_checkpoints.sql
```

**CLI:**

```bash
# Run a large review with a stable review ID so it can be resumed
prisma-review \
  --title "CRISPR gene editing: systematic review" \
  --pg-dsn "postgresql://user:pass@localhost/prisma_db" \
  --review-id "crispr-2026-001" \
  --synthesis-batch-size 20

# If interrupted, re-run the same command — completed batches are skipped automatically
prisma-review --title "..." --pg-dsn "..." --review-id "crispr-2026-001"
```

**Python API:**

```python
protocol = ReviewProtocol(
    title="CRISPR gene editing: systematic review",
    pg_dsn="postgresql://user:pass@localhost/prisma_db",
    review_id="crispr-2026-001",   # stable ID enables resume
    synthesis_batch_size=20,        # articles per synthesis chunk (default: 20)
    max_batch_retries=3,            # retries per failed batch (default: 3)
)
result = await pipeline.run(protocol)

# Re-run with same review_id → completed stages are skipped
result = await pipeline.run(protocol)

# Force a complete re-run
protocol.force_refresh = True
result = await pipeline.run(protocol)
```

**How it works:**

- Each pipeline stage (screening, charting, RoB, appraisal, narrative, synthesis) writes per-batch results to `pipeline_checkpoints` keyed by `(review_id, stage_name, batch_index)`.
- Synthesis is split into chunks of `synthesis_batch_size` articles. If there is more than one chunk, a dedicated merge agent combines the partial syntheses into a single coherent output — replacing the previous hardcoded top-20 limit.
- `CacheStore.load_completed_stages(review_id)` returns all stages where every batch is `complete`; the pipeline skips those stages on startup.
- `BatchMaxRetriesError` is raised if a batch exceeds `max_batch_retries` consecutive failures.
- When `pg_dsn` is not set, checkpointing is silently skipped and the pipeline runs as before.

## CLI Reference

```
prisma-review [OPTIONS]

Protocol:
  --title, -t          Review title / research question
  --objective          Detailed objective
  --population         PICO: Population
  --intervention       PICO: Intervention
  --comparison         PICO: Comparison
  --outcome            PICO: Outcome
  --inclusion          Inclusion criteria
  --exclusion          Exclusion criteria
  --registration       PROSPERO registration number

Search:
  --model, -m          OpenRouter model (default: anthropic/claude-sonnet-4)
  --databases          Databases to search (default: PubMed bioRxiv)
  --max-results        Max results per query (default: 20)
  --related-depth      Related article depth (default: 1)
  --hops               Citation hop depth 0-4 (default: 1)
  --biorxiv-days       bioRxiv lookback days (default: 180)
  --date-start         Start date YYYY-MM-DD
  --date-end           End date YYYY-MM-DD
  --rob-tool           RoB 2 | ROBINS-I | Newcastle-Ottawa Scale

Pipeline:
  --no-cache           Disable SQLite cache
  --extract-data       Enable per-study data extraction
  --auto               Skip plan confirmation; run end-to-end without prompts
  --max-plan-iterations  Max plan re-generation attempts before aborting (default: 3)
  --concurrency N      Max concurrent LLM calls per article step (default: 5, max: 20)

Cache (PostgreSQL):
  --pg-dsn             PostgreSQL DSN (or set PRISMA_PG_DSN env var)
  --force-refresh      Bypass cache and run fresh pipeline
  --cache-threshold    Similarity threshold for cache hit (default: 0.95)
  --cache-ttl-days     Cache entry TTL in days; 0=never expire (default: 30)

Output:
  --export, -e         Export formats: md json bib ttl jsonld (default: md)
  --rdf-store-path     Save pyoxigraph RDF store to this Turtle file path
  --interactive, -i    Interactive protocol setup
```

## Extending

### Add a New Agent

1. Define the output model in `models.py`:
```python
class MyOutput(BaseModel):
    field: str
    score: float
```

2. Create the agent in `agents.py`:
```python
my_agent = Agent(
    output_type=MyOutput,
    deps_type=AgentDeps,
    system_prompt="...",
    defer_model_check=True,
    name="my_agent",
)

async def run_my_agent(data: str, deps: AgentDeps) -> MyOutput:
    model = build_model(deps.api_key, deps.model_name)
    result = await my_agent.run(data, deps=deps, model=model)
    return result.output
```

3. Integrate into `pipeline.py`.

### Add a New Data Source

1. Create a client class in `clients.py` following the `PubMedClient` pattern.
2. Add it to `PRISMAReviewPipeline.__init__()`.
3. Add a search step in `pipeline.py`.

## Running E2E Tests

The `tests/e2e/` suite exercises the full review workflow through both the CLI and the Python API. All mock tests use `pydantic-ai` `TestModel` — no API key required.

```bash
# All e2e tests (mock mode — no API key needed)
pytest tests/e2e/ -v

# CLI tests only
pytest tests/e2e/test_cli_e2e.py -v

# Python API tests only
pytest tests/e2e/test_python_api_e2e.py -v

# Export format tests (requires tests/fixtures/minimal_review_result.json)
pytest tests/e2e/test_export_validation.py -v

# Full real-API smoke tests
export OPENROUTER_API_KEY="sk-..."
export RUN_E2E=1
pytest tests/e2e/ -m smoke -v
```

**Build the export fixture** (once, then commit):

```bash
export OPENROUTER_API_KEY="sk-..."
python scripts/build_e2e_fixture.py
```

See [specs/011-e2e-review-tests/quickstart.md](specs/011-e2e-review-tests/quickstart.md) for full details.

---

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| `pydantic-ai` | >=1.0 | Agent framework with typed outputs |
| `pydantic` | >=2.0 | Data validation and serialization |
| `httpx` | >=0.25 | Async-capable HTTP client |
| `psycopg[async]` | >=3.1 | Async PostgreSQL driver (optional) |
| `psycopg-pool` | >=3.1 | Async connection pooling (optional) |
| `rapidfuzz` | >=3.0 | Fuzzy string matching for cache similarity + source grounding |
| `rdflib` | >=6.0 | RDF graph construction and Turtle / JSON-LD serialization |
| `pyoxigraph` | >=0.3 | Fast in-process SPARQL store for queryable RDF output |

## License

Apache 2.0 — see [LICENSE](LICENSE).
