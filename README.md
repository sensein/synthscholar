# PRISMA Agent — Pydantic AI Systematic Review

A standalone, agent-based systematic literature review tool following **PRISMA 2020** guidelines. Built with [pydantic-ai](https://ai.pydantic.dev/) for structured LLM interactions and typed outputs via [OpenRouter](https://openrouter.ai/).

## Architecture

```
prisma-agent/
├── models.py      # Pydantic v2 models (Article, Protocol, Evidence, GRADE, etc.)
├── clients.py     # HTTP clients: PubMed (NCBI E-utilities), bioRxiv, SQLite cache
├── agents.py      # 9 pydantic-ai agents with typed outputs + runner functions
├── evidence.py    # LLM-powered evidence extraction (delegates to agent)
├── pipeline.py    # Async orchestrator — runs the full 15-step PRISMA pipeline
├── export.py      # Export: Markdown (PRISMA 2020 format), JSON, BibTeX
├── main.py        # Standalone CLI with argparse + interactive mode
└── README.md
```

### Design Principles

- **Agent-per-task**: Each PRISMA step that requires LLM reasoning has a dedicated pydantic-ai `Agent` with a typed `output_type`. No raw string parsing — the LLM returns validated Pydantic models.
- **No hardcoded heuristics**: Evidence extraction, screening, bias assessment, and synthesis are all handled by specialized LLM agents. No keyword lists or regex scoring.
- **Typed throughout**: Every data structure is a Pydantic `BaseModel` with validation. Structured outputs from agents are parsed and validated automatically by pydantic-ai.
- **Async pipeline**: The orchestrator uses `asyncio` for concurrent LLM calls (bias + GRADE + limitations run in parallel).
- **Standalone**: No web framework dependency. Runs as a CLI tool. Can be imported as a library.

## Quick Start

### Prerequisites

```bash
pip install pydantic-ai httpx pydantic
```

### Set API Key

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."

# Optional: for higher PubMed rate limits
export NCBI_API_KEY="your-ncbi-key"
```

### Run a Review

```bash
# Simple review
python main.py \
  --title "CRISPR gene therapy efficacy" \
  --inclusion "Clinical trials, human subjects, English" \
  --exclusion "Animal-only studies, reviews, commentaries"

# Full PICO specification
python main.py \
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
python main.py --interactive
```

### Use as a Library

```python
import asyncio
from models import ReviewProtocol
from pipeline import PRISMAReviewPipeline
from export import to_markdown, to_json

protocol = ReviewProtocol(
    title="Gut microbiome and depression",
    objective="Examine the relationship between gut microbiota composition and depressive disorders",
    pico_population="Adults with major depressive disorder",
    pico_intervention="Gut microbiome profiling",
    pico_comparison="Healthy controls",
    pico_outcome="Microbiome diversity, specific taxa abundance",
    inclusion_criteria="Human studies, English, 2018-2024",
    exclusion_criteria="Animal studies, reviews, case reports",
    max_hops=1,
    rob_tool="Newcastle-Ottawa Scale",
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
    md = to_markdown(result)
    Path("review.md").write_text(md)

    # Access structured data
    print(f"Included: {result.flow.included_synthesis} studies")
    for article in result.included_articles:
        rob = article.risk_of_bias.overall.value if article.risk_of_bias else "?"
        print(f"  [{article.pmid}] {article.short_author} ({article.year}) — RoB: {rob}")

    for span in result.evidence_spans[:5]:
        print(f"  Evidence [{span.paper_pmid}]: {span.text[:100]}...")

asyncio.run(run())
```

## Pipeline Steps (15-step PRISMA 2020)

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
| 13. Synthesis | `synthesis_agent` | `str` | Grounded narrative with PMID citations |
| 14. Bias + GRADE | `bias_summary_agent` + `grade_agent` | `str` + `GRADEAssessment` | Parallel assessment |
| 15. Limitations | `limitations_agent` | `str` | Review limitations section |

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

SQLite cache (`prisma_agent_cache.db`) stores:
- PubMed search results (72h TTL)
- Article metadata
- Full-text content
- Related article links
- bioRxiv search results

Disable with `--no-cache` or `enable_cache=False`.

## CLI Reference

```
python main.py [OPTIONS]

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

Output:
  --export, -e         Export formats: md json bib (default: md)
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

## License

Part of the AEP Knowledge Synthesis project.
