# PRISMAReviewPipeline

The main orchestrator. Instantiate once per review, then call `run()` or
`run_compare()`.

## Constructor

```python
PRISMAReviewPipeline(
    protocol: ReviewProtocol,
    api_key: str = "",              # OpenRouter key (or set OPENROUTER_API_KEY)
    model_name: str = "anthropic/claude-sonnet-4",
    pg_dsn: str | None = None,      # PostgreSQL DSN for caching
    cache_threshold: float = 0.95,
    cache_ttl_days: int = 30,
    concurrency: int = 5,           # parallel LLM calls per article (1–20)
    max_results: int = 20,          # articles per search query
    rob_tool: str = "RoB 2",
    extract_data: bool = False,
    biorxiv_days: int = 180,
    related_depth: int = 1,
    hops: int = 10,
)
```

## `run()`

Execute the full 18-step pipeline.

```python
async def run(
    update_callback: Callable[[str], None] | None = None,
    plan_confirm_callback: Callable[[ReviewPlan], Awaitable[bool]] | None = None,
    auto: bool = False,
    force_refresh: bool = False,
    max_plan_iterations: int = 3,
    max_articles: int | None = None,
    charting_template: ChartingTemplate | None = None,
    appraisal_config: CriticalAppraisalConfig | None = None,
) -> PRISMAReviewResult
```

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `update_callback` | Called with a status string after each pipeline step |
| `plan_confirm_callback` | Async function receiving `ReviewPlan`; return `True` to proceed, `False` to regenerate |
| `auto` | Skip plan confirmation entirely |
| `force_refresh` | Bypass cache even if a hit exists |
| `max_plan_iterations` | Max times to regenerate the search plan before raising `MaxIterationsReachedError` |
| `max_articles` | After deduplication, rerank by relevance and keep top N |
| `charting_template` | Custom `ChartingTemplate`; defaults to `default_charting_template()` |
| `appraisal_config` | Custom `CriticalAppraisalConfig`; defaults to `default_appraisal_config()` |

## `run_compare()`

Run in compare mode with multiple models.

```python
async def run_compare(
    models: list[str],
    consensus_model: str | None = None,
    update_callback: Callable[[str], None] | None = None,
    auto: bool = True,
    max_articles: int | None = None,
) -> CompareReviewResult
```

**Parameters:**

| Parameter | Description |
|-----------|-------------|
| `models` | List of ≥ 2 OpenRouter model names |
| `consensus_model` | Model used for consensus synthesis; defaults to `models[0]` |

## Pipeline Steps

```{raw} html
<ol class="pipeline-steps">
  <li><strong>Search Strategy</strong> — LLM generates PubMed + bioRxiv queries from PICO</li>
  <li><strong>PubMed Search</strong> — Fetches articles via PubMed E-utilities</li>
  <li><strong>bioRxiv Search</strong> — Fetches preprints from bioRxiv API</li>
  <li><strong>Related Articles</strong> — Expands via PubMed related-article API</li>
  <li><strong>Citation Hops</strong> — Traverses citation graph (configurable depth)</li>
  <li><strong>Deduplication</strong> — SHA-256 + fuzzy PMID/DOI dedup</li>
  <li><strong>Title/Abstract Screening</strong> — Batches of 15, fully parallel</li>
  <li><strong>Full-text Retrieval</strong> — Downloads PDF/HTML for included articles</li>
  <li><strong>Full-text Screening</strong> — Batches of 10, fully parallel</li>
  <li><strong>Evidence Extraction</strong> — Batches of 5, fully parallel</li>
  <li><strong>Data Extraction</strong> — Per-article, fully parallel</li>
  <li><strong>Risk of Bias</strong> — Per-article, fully parallel</li>
  <li><strong>Data Charting</strong> — Per-article, fully parallel</li>
  <li><strong>Critical Appraisal</strong> — Per-article, fully parallel</li>
  <li><strong>Narrative Rows</strong> — Per-article, fully parallel</li>
  <li><strong>Synthesis + GRADE</strong> — Parallel synthesis and certainty rating</li>
  <li><strong>Bias + Limitations</strong> — Parallel bias summary and limitations text</li>
  <li><strong>Assembly</strong> — Structured PRISMA 2020 document</li>
</ol>
```

## Exceptions

| Exception | Raised when |
|-----------|-------------|
| `PlanRejectedError` | User rejects all generated search plans |
| `MaxIterationsReachedError` | `max_plan_iterations` exceeded without approval |
| `BatchMaxRetriesError` | A screening or extraction batch exceeds max retries |

## ReviewProtocol

```python
class ReviewProtocol(BaseModel):
    title: str                      # Research question
    objective: str = ""
    pico_text: str = ""             # Free-text PICO (auto-built from fields below)
    population: str = ""
    intervention: str = ""
    comparison: str = ""
    outcome: str = ""
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    date_range_start: str = ""      # YYYY-MM-DD
    date_range_end: str = ""
    registration: str = ""          # PROSPERO number
    question: str = ""              # Alias for title
```
