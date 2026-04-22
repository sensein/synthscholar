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
- **Async pipeline**: The orchestrator uses `asyncio` for concurrent LLM calls (bias + GRADE + limitations run in parallel).
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

Run the same protocol through two or more LLMs in parallel. Article acquisition (PubMed/bioRxiv search, deduplication) runs once; all LLM-dependent steps (screening, synthesis, charting, appraisal) run independently per model. Results are merged into a single `CompareReviewResult` with per-field agreement indicators and an LLM-generated consensus synthesis.

#### CLI — compare mode

```bash
# Two-model compare; writes {slug}_compare.md, {slug}_compare.json, and per-model .md files
prisma-review \
  --title "CRISPR gene therapy efficacy" \
  --inclusion "Clinical trials, human subjects" \
  --exclusion "Animal-only studies" \
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

protocol = ReviewProtocol(
    title="CRISPR gene therapy efficacy",
    inclusion_criteria="Clinical trials, human subjects, English",
    exclusion_criteria="Animal-only studies, reviews",
)

async def run():
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        model_name="anthropic/claude-sonnet-4",  # used for search strategy; per-model LLM steps use their own model
        protocol=protocol,
    )

    compare_result = await pipeline.run_compare(
        models=["anthropic/claude-sonnet-4", "openai/gpt-4o"],
        auto_confirm=True,           # skip plan confirmation prompt
        consensus_model="anthropic/claude-sonnet-4",  # model for consensus synthesis (default: first in list)
        assemble_timeout=3600.0,     # per-model assembly timeout in seconds
    )

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

The pipeline's `confirm_callback` and `progress_callback` hooks make it straightforward to build a live UI on top of FastAPI. The pattern uses `asyncio.Event` to bridge the synchronous callback with an async HTTP round-trip, and Server-Sent Events (SSE) for real-time progress streaming.

#### Pattern 1 — Plan confirmation with polling

```python
import asyncio
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel as PydanticBase
from prisma_review_agent.models import (
    ReviewPlan, ReviewProtocol, PlanRejectedError, MaxIterationsReachedError,
    RubricSectionConfig,
)
from prisma_review_agent.pipeline import PRISMAReviewPipeline

app = FastAPI()

# In-memory session store — use Redis or a DB in production
_sessions: dict[str, dict] = {}


class ReviewRequest(PydanticBase):
    title: str
    inclusion: str = ""
    exclusion: str = ""
    section_output_formats: dict[str, str] = {}     # optional per-section formats
    rubric_section_config: list[dict] = []          # optional full config


class ConfirmRequest(PydanticBase):
    session_id: str
    response: str   # "yes" | "no" | feedback text


@app.post("/review/start")
async def start_review(req: ReviewRequest):
    session_id = str(uuid.uuid4())
    confirm_event: asyncio.Event = asyncio.Event()
    session: dict = {
        "confirm_event": confirm_event,
        "confirm_response": None,
        "plan": None,
        "progress": [],
        "status": "starting",
        "result": None,
    }
    _sessions[session_id] = session

    rubric_cfg = [RubricSectionConfig(**c) for c in req.rubric_section_config]
    protocol = ReviewProtocol(
        title=req.title,
        inclusion_criteria=req.inclusion,
        exclusion_criteria=req.exclusion,
        section_output_formats=req.section_output_formats,
        rubric_section_config=rubric_cfg,
    )
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        model_name="anthropic/claude-sonnet-4",
        protocol=protocol,
    )

    def confirm_callback(plan: ReviewPlan) -> bool | str:
        """Called by the pipeline when a plan is ready — blocks until UI responds."""
        session["plan"] = plan.model_dump()
        session["status"] = "awaiting_confirmation"
        confirm_event.clear()
        # Run the event wait in the event loop — pipeline resumes when /confirm fires
        asyncio.get_event_loop().run_until_complete(confirm_event.wait())
        return session["confirm_response"]

    def progress_callback(message: str) -> None:
        session["progress"].append(message)
        session["status"] = "running"

    asyncio.create_task(_run_pipeline(pipeline, session, confirm_callback, progress_callback))
    return {"session_id": session_id, "status": "starting"}


async def _run_pipeline(pipeline, session, confirm_cb, progress_cb):
    try:
        result = await pipeline.run(
            confirm_callback=confirm_cb,
            progress_callback=progress_cb,
        )
        session["result"] = result.model_dump(mode="json")
        session["status"] = "complete"
    except PlanRejectedError:
        session["status"] = "rejected"
    except MaxIterationsReachedError as e:
        session["status"] = f"max_iterations: {e}"
    except Exception as e:
        session["status"] = f"error: {e}"


@app.get("/review/{session_id}/plan")
async def get_plan(session_id: str):
    """Poll until plan is ready (status = 'awaiting_confirmation'), then show it in the UI."""
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session["status"] == "awaiting_confirmation" and session["plan"]:
        return {"status": "awaiting_confirmation", "plan": session["plan"]}
    return {"status": session["status"]}


@app.post("/review/confirm")
async def confirm_plan(req: ConfirmRequest):
    """POST the user's decision here — pipeline unblocks and continues."""
    session = _sessions.get(req.session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    response = req.response.strip()
    if response.lower() in ("yes", "y", ""):
        session["confirm_response"] = True
    elif response.lower() in ("no", "abort"):
        session["confirm_response"] = False
    else:
        session["confirm_response"] = response  # feedback → plan re-generation
    session["confirm_event"].set()
    return {"status": "acknowledged"}


@app.get("/review/{session_id}/status")
async def get_status(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "status": session["status"],
        "progress": session["progress"],
        "result": session["result"],
    }
```

#### Pattern 2 — Real-time progress with Server-Sent Events (SSE)

For a live progress feed (like a terminal-style log in the UI), replace polling with SSE:

```python
import asyncio
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

app = FastAPI()

@app.get("/review/{session_id}/stream")
async def stream_progress(session_id: str):
    """Stream pipeline progress events as SSE to the browser."""
    session = _sessions.get(session_id)
    if not session:
        return StreamingResponse(iter([]), media_type="text/event-stream")

    last_idx = 0

    async def event_generator():
        nonlocal last_idx
        while True:
            messages = session["progress"]
            if len(messages) > last_idx:
                for msg in messages[last_idx:]:
                    yield f"data: {msg}\n\n"
                last_idx = len(messages)
            if session["status"] in ("complete", "rejected", "error") or \
               session["status"].startswith("error") or \
               session["status"].startswith("max_iterations"):
                yield f"data: [DONE] {session['status']}\n\n"
                break
            if session["status"] == "awaiting_confirmation":
                yield f"event: plan_ready\ndata: awaiting_confirmation\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

> **Note**: The in-memory `_sessions` dict works for single-process development. In production, use Redis pub/sub (for SSE fan-out) and store session state in a persistent store. The `asyncio.get_event_loop().run_until_complete(event.wait())` call in `confirm_callback` works in a single-threaded asyncio loop; if the pipeline runs in a thread pool, use `loop.call_soon_threadsafe(event.set)` instead.

#### Pattern 3 — Configurable assembly timeout

`pipeline.run()` accepts an `assemble_timeout` parameter (default `3600.0` seconds) that wraps the two-wave LLM gather with `asyncio.wait_for`. Expose it in your API so callers can set shorter timeouts for testing or longer ones for very large reviews:

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel as PydanticBase
from prisma_review_agent.pipeline import PRISMAReviewPipeline
from prisma_review_agent.models import ReviewProtocol
import asyncio, uuid

app = FastAPI()
_sessions: dict[str, dict] = {}


class ReviewRequest(PydanticBase):
    title: str
    inclusion: str = ""
    exclusion: str = ""
    assemble_timeout: float = 3600.0   # client-supplied; guard against absurd values server-side


@app.post("/review/start")
async def start_review(req: ReviewRequest):
    # Hard cap: never allow a timeout > 2 hours from an external caller
    timeout = min(req.assemble_timeout, 7200.0)

    session_id = str(uuid.uuid4())
    session: dict = {"status": "running", "result": None, "progress": []}
    _sessions[session_id] = session

    protocol = ReviewProtocol(
        title=req.title,
        inclusion_criteria=req.inclusion,
        exclusion_criteria=req.exclusion,
    )
    pipeline = PRISMAReviewPipeline(
        api_key="sk-or-v1-...",
        model_name="anthropic/claude-sonnet-4",
        protocol=protocol,
    )

    asyncio.create_task(_run_with_timeout(pipeline, session, timeout))
    return {"session_id": session_id}


async def _run_with_timeout(pipeline, session: dict, timeout: float):
    try:
        result = await pipeline.run(
            auto_confirm=True,
            assemble_timeout=timeout,
        )
        session["result"] = result.model_dump(mode="json")
        session["status"] = "complete"
    except asyncio.TimeoutError:
        session["status"] = "timeout"
        session["error"] = f"Assembly exceeded {timeout:.0f}s limit"
    except Exception as e:
        session["status"] = f"error: {e}"


@app.get("/review/{session_id}/status")
async def get_status(session_id: str):
    session = _sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if session["status"] == "timeout":
        raise HTTPException(504, session.get("error", "Assembly timed out"))
    return {"status": session["status"], "result": session.get("result")}
```

**Key points:**
- Cap the caller-supplied timeout server-side (`min(req.assemble_timeout, 7200.0)`) to prevent runaway tasks.
- `asyncio.TimeoutError` is raised inside the background task — catching it there and writing `"timeout"` to the session lets `/status` return a 504.
- The default `3600.0` s is appropriate for production reviews with 10–50 included studies. Use `30.0`–`120.0` s for smoke-test environments.

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

## Pipeline Steps (17-step Enhanced PRISMA)

| Step | Agent | Output Type | Description |
|------|-------|-------------|-------------|
| 1. Search Strategy | `search_strategy_agent` | `SearchStrategy` | Generates PubMed + bioRxiv queries from protocol |
| 2. PubMed Search | — (HTTP) | `list[Article]` | E-utilities esearch + efetch |
| 3. bioRxiv Search | — (HTTP) | `list[Article]` | bioRxiv API keyword matching |
| 4. Related Articles | — (HTTP) | `list[str]` | elink neighbor_score |
| 5. Citation Hops | — (HTTP) | `list[Article]` | Forward (cited-by) + backward navigation |
| 6. Deduplication | — (logic) | `list[Article]` | DOI/PMID dedup |
| 7. Title/Abstract Screening | `screening_agent` | `ScreeningBatchResult` | LLM batch screening (inclusive) |
| 8. Full-text Retrieval | — (HTTP) | `dict[str, str]` | PMC efetch |
| 9. Full-text Screening | `screening_agent` | `ScreeningBatchResult` | LLM batch screening (strict) |
| 10. Evidence Extraction | `evidence_extraction_agent` | `BatchEvidenceExtraction` | LLM identifies claims + evidence spans |
| 11. Data Extraction | `data_extraction_agent` | `StudyDataExtraction` | Per-study structured data |
| 12. Risk of Bias | `rob_agent` | `RiskOfBiasResult` | Per-study RoB 2 / ROBINS-I / NOS |
| 13. Data Charting | `data_charting_agent` | `DataChartingRubric` | Structured charting across 7 sections (A-G) |
| 14. Critical Appraisal | `critical_appraisal_agent` | `CriticalAppraisalRubric` | Quality assessment across 4 domains |
| 15. Narrative Rows | `narrative_row_agent` | `PRISMANarrativeRow` | Condensed 6-cell summary format |
| 16. Synthesis | `synthesis_agent` | `str` | Grounded narrative with PMID citations |
| 17. Bias + GRADE | `bias_summary_agent` + `grade_agent` | `str` + `GRADEAssessment` | Parallel assessment |
| 18. Limitations | `limitations_agent` | `str` | Review limitations section |

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
| GPT-4o | `openai/gpt-4o` | Strong general performance |
| DeepSeek Chat | `deepseek/deepseek-chat` | Cost-effective |
| Llama 3.1 70B | `meta-llama/llama-3.1-70b-instruct` | Open-source option |

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
