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
├── export.py           # Export: Markdown, JSON, BibTeX, CSV formats
├── main.py             # Standalone CLI with argparse + interactive mode
└── prisma_review_agent/
    └── cache/          # PostgreSQL cache sub-package
        ├── models.py        # CacheEntry, SimilarityConfig, StoredArticle
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

**`confirm_callback` return value semantics:**

| Return value | Meaning | Pipeline action |
|---|---|---|
| `True` | Plan approved | Continue to article retrieval |
| `False` | Plan rejected | Raise `PlanRejectedError` |
| `""` (empty string) | Treated as approval | Continue to article retrieval |
| `"<feedback text>"` | Re-generate with feedback | Call agent again with feedback; increment iteration |

### FastAPI Integration

Bridge the synchronous `confirm_callback` with an async HTTP round-trip using `asyncio.Event`:

```python
import asyncio
from fastapi import FastAPI
from pydantic import BaseModel as PydanticBase
from prisma_review_agent.models import ReviewPlan, ReviewProtocol, PlanRejectedError
from prisma_review_agent.pipeline import PRISMAReviewPipeline

app = FastAPI()

# In-memory session store (use Redis/DB in production)
_sessions: dict[str, dict] = {}


class ReviewRequest(PydanticBase):
    title: str
    inclusion: str = ""
    exclusion: str = ""


class ConfirmRequest(PydanticBase):
    session_id: str
    response: str  # "yes", "no", or feedback text


@app.post("/review/start")
async def start_review(req: ReviewRequest):
    session_id = str(id(req))  # use uuid4() in production
    event: asyncio.Event = asyncio.Event()
    _sessions[session_id] = {"event": event, "response": None, "plan": None}

    def capture_plan(plan: ReviewPlan) -> bool | str:
        _sessions[session_id]["plan"] = plan.model_dump()
        event.clear()
        # Block the pipeline until the /review/confirm endpoint fires the event
        asyncio.get_event_loop().run_until_complete(event.wait())
        return _sessions[session_id]["response"]

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

    # Run the pipeline in the background so the HTTP response returns immediately
    asyncio.create_task(_run_pipeline(pipeline, session_id, event))
    # Wait briefly for the plan to be generated before returning
    await asyncio.sleep(0)
    return {"session_id": session_id, "status": "awaiting_plan"}


async def _run_pipeline(pipeline, session_id: str, event: asyncio.Event):
    session = _sessions[session_id]
    try:
        result = await pipeline.run(confirm_callback=session["callback"])
        session["result"] = result.model_dump(mode="json")
        session["status"] = "complete"
    except PlanRejectedError:
        session["status"] = "rejected"
    except Exception as e:
        session["status"] = f"error: {e}"


@app.get("/review/{session_id}/plan")
async def get_plan(session_id: str):
    """Poll this endpoint until plan is ready, then display it to the user."""
    session = _sessions.get(session_id)
    if not session or not session.get("plan"):
        return {"status": "generating"}
    return {"status": "awaiting_confirmation", "plan": session["plan"]}


@app.post("/review/confirm")
async def confirm_plan(req: ConfirmRequest):
    """User's browser POSTs here with their decision."""
    session = _sessions.get(req.session_id)
    if not session:
        return {"error": "session not found"}
    response = req.response.strip()
    if response.lower() in ("yes", "y", ""):
        session["response"] = True
    elif response.lower() in ("no", "abort"):
        session["response"] = False
    else:
        session["response"] = response  # feedback text
    session["event"].set()  # unblock the pipeline
    return {"status": "acknowledged"}
```

> **Note**: The example above uses a simple in-memory store. For production, store session state in Redis or a database and use `asyncio.Queue` or a proper future/event mechanism to bridge the HTTP round-trip with the pipeline callback.

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
