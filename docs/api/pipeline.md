# PRISMAReviewPipeline

The main orchestrator. Instantiate once per review, then call `run()` (single-model) or pass to `run_compare()` (multi-model).

## Constructor

```python
from synthscholar.pipeline import PRISMAReviewPipeline
from synthscholar.models import ReviewProtocol

pipeline = PRISMAReviewPipeline(
    api_key: str,                                          # OpenRouter key (required)
    model_name: str = "anthropic/claude-sonnet-4",
    ncbi_api_key: str = "",                                # falls back to NCBI_API_KEY env
    email: str = "",                                       # falls back to SYNTHSCHOLAR_EMAIL env
    api_keys: dict[str, str] | None = None,                # {"semantic_scholar": "...", "core": "..."}
    protocol: ReviewProtocol | None = None,                # the review specification
    enable_cache: bool = True,                             # SQLite article cache
    max_per_query: int = 20,                               # PubMed results per query
    related_depth: int = 1,                                # related-articles expansion depth
    biorxiv_days: int = 180,                               # bioRxiv lookback window
)
```

The pipeline does not accept concurrency, RoB tool, hops, date ranges, or extraction toggles directly — those live on the [`ReviewProtocol`](models.md) you pass in (`protocol.article_concurrency`, `protocol.rob_tool`, `protocol.max_hops`, `protocol.date_range_start`/`date_range_end`).

### Auth resolution

Every credential field uses the precedence **explicit constructor argument → env var → default**:

| Field | Env var | Default if all absent |
|---|---|---|
| `api_key` (OpenRouter) | `OPENROUTER_API_KEY` | error at first agent call |
| `ncbi_api_key` | `NCBI_API_KEY` | empty (3 req/s rate limit) |
| `email` | `SYNTHSCHOLAR_EMAIL` | `tekraj@mit.edu` (`NCBI_EMAIL` constant) |
| `api_keys["semantic_scholar"]` | `SEMANTIC_SCHOLAR_API_KEY` | empty (lower SS rate limits) |
| `api_keys["core"]` | `CORE_API_KEY` | empty (CORE provider silently disabled) |

The resolved credentials live on `pipeline.pubmed.api_key`, `pipeline.full_text_resolver.email`, and `pipeline.full_text_resolver.api_keys` so compare-mode sub-pipelines can inherit them.

## `run()`

Execute the full PRISMA pipeline.

```python
async def run(
    progress_callback: Callable[[str], None] | None = None,
    data_items: list[str] | None = None,
    auto_confirm: bool = False,
    confirm_callback: Callable[[ReviewPlan], bool | str] | None = None,
    max_plan_iterations: int = 3,
    output_synthesis_style: str = "paragraph",
    assemble_timeout: float = 3600.0,
    review_id: str | None = None,
) -> PRISMAReviewResult
```

| Parameter | Description |
|---|---|
| `progress_callback` | Sync callable invoked with each pipeline progress message (e.g. `"Screening 30 articles..."`). Use it to feed an SSE stream. |
| `data_items` | List of fields to extract per study (e.g. `["sample_size", "primary_outcome"]`). When omitted, the data-extraction leg of the per-article DAG is skipped. |
| `auto_confirm` | Skip the search-strategy confirmation gate. Defaults to `False`; the pipeline auto-promotes to `True` when stdin is non-interactive (logged). |
| `confirm_callback` | Receives the generated `ReviewPlan` and returns `True` (proceed), `False` (abort with `PlanRejectedError`), or a feedback string (regenerate the plan; up to `max_plan_iterations`). |
| `max_plan_iterations` | Cap on plan regeneration before raising `MaxIterationsReachedError`. |
| `output_synthesis_style` | `"paragraph"` or `"question_answer"` — controls the thematic-synthesis output shape. |
| `assemble_timeout` | Wall-clock budget for the structured PrismaReview assembly stage. |
| `review_id` | Identifier used by the PostgreSQL checkpoint store for resumable runs. |

## Compare mode

Compare mode is invoked through the standalone helper, not a method on the pipeline:

```python
from synthscholar.compare import run_compare

result = await run_compare(
    pipeline=pipeline,                    # already-built PRISMAReviewPipeline
    models=[
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
        "google/gemini-2.5-pro",
    ],
    progress_callback=on_progress,
    data_items=None,
    auto_confirm=True,
    confirm_callback=None,
    max_plan_iterations=3,
    consensus_model=None,
    output_synthesis_style="paragraph",
    assemble_timeout=3600.0,
)
# result.results[model_name] holds each per-model PRISMAReviewResult
# result.field_agreement holds the cross-model agreement report
```

`run_compare` validates that `models` has 2–5 unique entries (duplicates emit a warning and are ignored). Article acquisition runs **once** on the parent pipeline; only the LLM-driven steps fan out per model. Each sub-pipeline inherits `pipeline.pubmed.api_key`, `pipeline.full_text_resolver.email`, and `pipeline.full_text_resolver.api_keys` so polite-pool credentials follow.

## Per-article DAG

Steps 11–15 (data extraction, RoB, charting, appraisal, narrative) run as a fused **per-article DAG**. Each article task spawns three sibling coroutines that complete via `asyncio.gather`:

```
                  article (acquires _dag_sem slot)
                  │
         ┌────────┼────────┐
         ▼        ▼        ▼
        RoB    Extract   Chart
                           │
                       Appraise
                           │
                        Narrate
```

- **RoB** and **Extract** are independent — they fire as soon as the article enters the DAG.
- The **Chart → Appraise → Narrate** chain is internally sequential because each step consumes the previous step's output.
- `proto.article_concurrency` (default 5, max 20) caps the number of article DAGs in flight. With concurrency=N this fans out to up to **3N** in-flight LLM calls (RoB + Extract + one of the chain legs per article).
- A failed leg drops the article from `ft_included` only if it broke the chain (i.e. charting failure → article excluded). RoB and Extract failures are best-effort: they log and leave the field `None`.
- Each leg with DB checkpointing (`STAGE_ROB`, `STAGE_CHARTING`, `STAGE_APPRAISAL`, `STAGE_NARRATIVE`) uses `_load_or_run_batch` — partial resumes are per-article-per-stage.

This eliminated four corpus-wide barriers the older block-by-block layout had between extract → RoB → chart → appraise → narrate. Slow articles no longer hold up faster ones.

## Synthesis layer

After the per-article DAG completes, the synthesis layer runs:

1. **Synthesis** (`run_synthesis`) — single call when the corpus fits `SYNTHESIS_BATCH_CHARS = 80_000` chars of article-block context. Otherwise: parallel partials sharded by char budget, merged via `run_synthesis_merge_agent` (LLM-mediated merge).
2. **Grounding validation** — every claim in the synthesis is fuzzy-matched back to its source.
3. **Gather #1** — `bias_summary`, `limitations`, `introduction`, and `GRADE` (one task per outcome) run concurrently in a single `asyncio.gather`.
4. **Gather #2** — `conclusions` and `structured_abstract` run concurrently (both depend on synthesis + GRADE summary).
5. **Assembly** — themes, quantitative analysis, and document sections are merged into the final `PrismaReview` via `run_thematic_synthesis` (parallel map-reduce when corpus exceeds `THEMATIC_BATCH_CHARS = 60_000`; deterministic merge via `_merge_thematic_results`).

## Pipeline steps (top-level)

| # | Step | Concurrency |
|---|---|---|
| 1 | Search Strategy (LLM) | sync |
| 2 | PubMed search | sequential per query |
| 3 | bioRxiv / medRxiv search | sequential per query |
| 4 | Related-articles expansion | sequential per hop |
| 5 | Multi-hop citation navigation | sequential per hop |
| 6 | Deduplication | in-memory |
| 7 | Title/abstract screening | batches of 15, parallel |
| 8 | Full-text retrieval (PMC + Europe PMC + DOI chain + PyMuPDF) | per-article, parallel |
| 9 | Full-text eligibility screening | batches of 10, parallel |
| 10 | Evidence-span extraction | batches of 5, parallel |
| **11–15** | **Per-article DAG**: RoB ∥ Extract ∥ (Chart → Appraise → Narrate) | **fused, parallel across articles** |
| 16 | Grounded synthesis | single call or parallel map-reduce |
| 17 | Grounding validation | sync |
| 18a | Gather #1: bias + limitations + intro + GRADE-per-outcome | concurrent |
| 18b | Gather #2: conclusions + abstract | concurrent |
| 18c | Assembly: themes + quantitative + final PrismaReview | mixed sync + parallel |

## PRISMA flow counts (Item 16a)

`result.flow` is a [`PRISMAFlowCounts`](models.md) populated during discovery from each `Article.source` value. The per-database tally is computed by `_apply_per_db_tally` after every search phase, so the totals stay consistent if you inspect `result.flow` mid-run.

| Field | Meaning | Sourced from |
|---|---|---|
| `db_pubmed` | Articles identified via PubMed | `Article.source == "pubmed_search"` |
| `db_biorxiv` | Articles from bioRxiv preprint search | `Article.source == "biorxiv"` |
| `db_medrxiv` | Articles from medRxiv preprint search | `Article.source == "medrxiv"` |
| `db_related` | Added via PubMed related-articles expansion | `Article.source.startswith("related_")` |
| `db_hops` | Added via citation-hop navigation | `Article.source.startswith("hop_")` |
| `db_other_sources: dict[str, int]` | Any provider not in the named set (Europe PMC, OpenAlex, CrossRef, DOAJ, Semantic Scholar, …) keyed by source name | All other non-empty `Article.source` values |
| `total_identified` | Sum of all per-DB buckets after dedup | — |

**Per-publication source provenance** lives on each [`Article.source`](models.md). For included studies, the same information is duplicated more formally on [`DataChartingRubric.database_retrieved`](models.md) (Section A) and [`SourceMetadata.database_retrieved_from`](models.md), and the full search trail is on [`result.search_iterations`](../guides/provenance.md) (one record per query / citation hop / related-articles round, with `database`, `query`, `seed_pmids`, `new_pmids`, `cumulative_count`, `duration_ms`).

## Per-disorder synthesis

`run_search_synthesis` lets the LLM choose a grouping dimension. For deterministic, reproducible per-disorder strata use `run_per_disorder_synthesis` from [`synthscholar.agents`](https://github.com/sensein/synthscholar/blob/main/synthscholar/agents.py):

```python
async def run_per_disorder_synthesis(
    articles: list[Article],
    disorder_labels: dict[str, str],     # {pmid: cohort}
    deps: AgentDeps,
    *,
    topic: str = "",
    min_articles_per_disorder: int = 1,
) -> PerDisorderSynthesis
```

Bucketing is case-insensitive and whitespace-collapsed; the original casing is preserved on `GroupSummary.label`. Articles missing from `disorder_labels` are excluded from synthesis and counted in `PerDisorderSynthesis.unlabeled_count`. Per-bucket calls run in parallel via `asyncio.gather`, and `GroupSummary.label` / `n_studies` are overwritten deterministically — LLM drift on those fields is ignored.

**Grounding source.** Each article block fed to the per-bucket LLM call carries title + abstract, plus a full-text excerpt (up to 2 000 chars) when the article has resolved full text and any pre-extracted key findings when the article has been through `pipeline.run()`'s extraction step. Numeric claims in `aggregate_finding` are grounded against the richest of these sources that's present on each article — abstract-only articles still work, but post-pipeline corpora produce strata grounded against full-text excerpts. The same helper (`_summarise_article_for_search`) feeds `run_search_synthesis`, so both flows share this behaviour.

Use `disorder_labels_from_rubrics(result.data_charting_rubrics)` to build the label dict from a finished review's charted rubrics; pass any other `dict[str, str]` for externally-curated labels.

## Exceptions

| Exception | Raised when |
|---|---|
| `PlanRejectedError` | `confirm_callback` returned `False` for all generated plans |
| `MaxIterationsReachedError` | `max_plan_iterations` exceeded without approval |
| `BatchMaxRetriesError` | A screening, charting, or appraisal batch exhausted retries |

## See also

- [`ReviewProtocol`](models.md#reviewprotocol) — the input specification (PICO, databases, RoB tool, charting questions, batch budgets, …)
- [`PRISMAReviewResult`](models.md#prismaresult) — the output payload
- [Architecture overview](../architecture.md)
- [Compare mode guide](../guides/compare-mode.md)
- [FastAPI integration guide](../guides/fastapi.md)
