
# SynthScholar — PRISMA 2020 Systematic Review Agent  
  
A multi-agent system for automated, PRISMA-2020-guided systematic literature reviews.  
  
**[Documentation](https://github.com/sensein/synthscholar/tree/main/docs)** · [Source](https://github.com/sensein/synthscholar) · [Issues](https://github.com/sensein/synthscholar/issues)  
  
---  
## What's included  
  
- **PRISMA 2020 pipeline** — 18 async steps and 16 specialised pydantic-ai agents, from search-strategy generation through two-stage screening, evidence extraction, RoB, data charting, critical appraisal, narrative rows, grounded synthesis, GRADE (per outcome, run concurrently), and a structured abstract with PRISMA flow counts.  
- **Multi-source OA discovery** — built-in providers for PubMed, bioRxiv, medRxiv, Europe PMC, OpenAlex, CrossRef, DOAJ, Semantic Scholar, arXiv, CORE, and Unpaywall.  
- **Full-text resolver chain** — for each included article, tries PMC OA → Europe PMC OA-XML → preprint PDF (canonical `{doi}v{N}.full.pdf`, Cloudflare-aware) → DOI resolvers (Unpaywall → OpenAlex → Semantic Scholar) and parses any reachable PDF with [PyMuPDF](https://pymupdf.readthedocs.io/).  
- **Source grounding** — every evidence span is fuzzy-matched back to its source article; ungrounded text is dropped.  
- **Process provenance** — every result records its full configuration (presence-only env-var capture, never values), every plan iteration with operator feedback, every search query / citation hop, and one-line tool-call summaries plus token / latency telemetry per LLM call. Each step is tagged with its iteration mode (zero-shot vs `iterative_with_human_feedback` / `iterative_with_fallback` / `hierarchical_reduce` / etc.). See the [Provenance guide](https://github.com/sensein/synthscholar/blob/main/docs/guides/provenance.md).  
- **Parallel map-reduce synthesis** — for corpora that exceed a per-call char budget, articles are sharded into char-budgeted batches that run in parallel; partials are merged via an LLM (synthesis) or deterministically (thematic synthesis).  
- **Multi-model compare mode** — run one protocol across 2–5 LLMs in parallel and produce a field-level agreement report.  
- **PostgreSQL cache + checkpoints + telemetry** — similarity-keyed result cache, resumable runs, and an optional `review_telemetry` audit table (migration 005) holding the full provenance trail per review.  
- **Multiple export formats** — Markdown, JSON, BibTeX, Turtle, JSON-LD (via the SLR Ontology, reusing PROV-O / FaBiO / BIBO / OA — including activity nodes for every plan iteration, search iteration, and agent invocation).  
- **Configurable RoB tools** — RoB 2, ROBINS-I, Newcastle-Ottawa, QUADAS-2.  
---  
  
## Install  
  
```bash  
pip install synthscholar                  # corepip install "synthscholar[fulltext]"      # adds PDF parsing (PyMuPDF) + arXiv (feedparser)
```  
  
From source:  
  
```bash  
git clone https://github.com/sensein/synthscholar.gitcd synthscholaruv sync --extra fulltext                  # or: pip install -e ".[fulltext]"
```  
  
Requires Python 3.11 or newer. PostgreSQL 15+ is optional and only needed for the protocol-similarity cache and resumable-run checkpoints.  
  
---  

## Quick start  
  
### 1. Set your API keys  
  
```bash  
export OPENROUTER_API_KEY="sk-or-v1-..."        # required  
export NCBI_API_KEY="..."                       # optional — raises PubMed rate limits 3 → 10 req/s
export SYNTHSCHOLAR_EMAIL="you@example.com"     # optional — Open Access (OA) providers (Unpaywall requires it)
export SEMANTIC_SCHOLAR_API_KEY="..."           # optional — higher Semantic Scholar rate limits + OA PDF lookups
export CORE_API_KEY="..."                       # optional — enables the CORE OA aggregator (no-op if unset)
```  
  
All five are read automatically. **Precedence: explicit constructor argument > env var > built-in default.**  
  
- `OPENROUTER_API_KEY` — required. Used by every LLM agent.

- `NCBI_API_KEY` — optional. Lifts the PubMed E-utilities rate limit from 3 → 10 req/s, so search and citation-hop steps finish ~3× faster. Without it the pipeline still works; large reviews just take longer.

- `SYNTHSCHOLAR_EMAIL` — optional but recommended. This email is used as a contact in the User-Agent header for all Open Access (OA) HTTP requests, and it’s also included as the email= parameter when using Unpaywall (as required by their terms of service).

- `SEMANTIC_SCHOLAR_API_KEY` — optional but **recommended**. Without a key, Semantic Scholar's public tier rate-limits to ~1 req/s, which becomes the bottleneck on the DOI-resolver chain (Unpaywall → OpenAlex → **Semantic Scholar** → PDF download) for reviews with many included articles. With a free key, rate limits jump enough that a 200-article review's full-text resolution finishes in minutes instead of hours. Visit [https://www.semanticscholar.org/product/api](https://www.semanticscholar.org/product/api) to get the API key.

- `CORE_API_KEY` — optional but **recommended**. CORE is a world-wide OA aggregator that **requires a key** — no public anonymous tier. Without it the CORE provider is silently skipped during multi-source search. Visit [https://core.ac.uk/](https://core.ac.uk/) for API key. It contains more **than 57M full texts and more than 449M searchable research papers from more than 15K data providers and 150 countries**.

  
  

**Why two keys?** They serve different roles. Semantic Scholar is a DOI **resolver** — when an article has no PMC copy and no Europe PMC OA-XML, Semantic Scholar's `openAccessPdf` field is often what unlocks the full text. CORE is a **discovery** source — it widens the initial search across thousands of OA repositories beyond PubMed/bioRxiv. You can run productively without either; supplying one improves throughput, supplying both expands coverage.
  
### 2. Run a review (CLI)  
  
Minimal — title only, prompts for the rest interactively:  
  
```bash  
synthscholar --title "CRISPR gene therapy for sickle cell disease"
```  
  
Full PICO specification, mirroring the Python example below:  
  
```bash  
synthscholar \
  --title "CRISPR gene therapy for sickle cell disease" \
  --population "patients with sickle cell disease" \
  --intervention "CRISPR gene therapy" \
  --comparison "standard of care or placebo" \
  --outcome "haematologic response, transfusion independence" \
  --inclusion "Clinical trials in humans, English-language" \
  --exclusion "Animal-only studies, reviews, editorials" \
  --databases PubMed bioRxiv medRxiv \
  --rob-tool "RoB 2" \
  --date-start 2018-01-01 \
  --auto \
  --export md json turtle
```  
  
Useful flags:  
  
- `--compare-models claude-sonnet-4 gpt-4o gemini-2.5-pro` — run in compare mode to execute the synthesis with multiple LLMs and compare their results.
- `--auto` — skip the interactive search-strategy confirmation, i.e., human-in-loop valiation.  
- `--no-cache` — bypass the protocol-similarity cache and re-run from scratch.  
- `--max-results N`, `--related-depth N`, `--hops N` — tune search breadth.  
- `--export md json turtle jsonld` —  output formats for results.  
  
Run `synthscholar --help` for the full list, or see the documentation.  
  
### Auth flags on the CLI  
  
Every env var from the previous section has a matching `--flag` form. Precedence is **CLI flag > env var > unset/default**.  
  
| Env var | CLI flag |  
|---|---|  
| `OPENROUTER_API_KEY` | `--api-key` |  
| `NCBI_API_KEY` | `--ncbi-api-key` |  
| `SYNTHSCHOLAR_EMAIL` | `--email` |  
| `SEMANTIC_SCHOLAR_API_KEY` | `--semantic-scholar-key` |  
| `CORE_API_KEY` | `--core-key` |  
  
```bash  
synthscholar \
  --title "..." \
  --api-key "sk-or-v1-..." \
  --ncbi-api-key "..." \
  --email "you@example.com" \
  --semantic-scholar-key "..." \
  --core-key "..."
```  
  
### 3. Run a review (Python)  
  
```python  
import asyncio  
from synthscholar.models import ReviewProtocol  
from synthscholar.pipeline import PRISMAReviewPipeline  
  
protocol = ReviewProtocol(  
 question="What is the efficacy of CRISPR-based therapies for sickle cell disease?", pico_population="patients with sickle cell disease", pico_intervention="CRISPR gene therapy", pico_outcome="haematologic response, transfusion independence", inclusion_criteria="Clinical trials in humans, English-language", databases=["PubMed", "bioRxiv", "medRxiv"],)  
  
pipeline = PRISMAReviewPipeline(  
 api_key="<openrouter-key>",          # required (or OPENROUTER_API_KEY env var, read by agents) ncbi_api_key="<ncbi-key>",           # or rely on NCBI_API_KEY env var email="you@example.com",             # or rely on SYNTHSCHOLAR_EMAIL env var api_keys={                            # or rely on SEMANTIC_SCHOLAR_API_KEY / CORE_API_KEY env vars "semantic_scholar": "<s2-key>", "core": "<core-key>", }, protocol=protocol,)  
result = asyncio.run(pipeline.run())  
print(result.synthesis_text)  
  
# Process provenance — see docs/guides/provenance.md  
print(f"{len(result.plan_iterations)} plan iterations, "  
 f"{len(result.agent_invocations)} LLM invocations, " f"{len(result.search_iterations)} search rounds")for pi in result.plan_iterations:  
 print(f"  iter {pi.iteration_index}: {pi.decision} — {pi.user_feedback[:60]}")  
# Per-database PRISMA identification numbers (Item 16a)  
f = result.flow  
print(f"PubMed={f.db_pubmed} bioRxiv={f.db_biorxiv} medRxiv={f.db_medrxiv} "  
 f"related={f.db_related} hops={f.db_hops} other={f.db_other_sources}")  
# Per-publication source provenance — every Article carries the database it  
# was identified from ("pubmed_search", "biorxiv", "medrxiv", "related_{N}",  
# "hop_{N}", or an OA-provider name when MultiSourceManager is wired in).  
for art in result.included_articles[:3]:  
 print(f"  {art.pmid} ← {art.source}")  
```  
  
Every auth field falls back to its env var when omitted, so the same call works in dev (everything in env) and in production (explicit dict from a secret manager).  
  
Every result carries full process provenance — `result.run_configuration` (model, package version, env-var presence — never values), `result.plan_iterations` (every plan version + operator feedback), `result.search_iterations` (each query, citation hop, related-articles round), and `result.agent_invocations` (per-call token counts, retries, tool-call summary, prompt snapshot). Per-publication source attribution lives on `Article.source`; per-database PRISMA identification numbers live on `result.flow` (`db_pubmed`, `db_biorxiv`, `db_medrxiv`, `db_related`, `db_hops`, plus `db_other_sources: dict[str, int]` for any provider not in the named set). The Markdown / Turtle / JSON-LD exporters surface this; PostgreSQL persists the full trail to a `review_telemetry` row when migration 005 is applied.  
  
For full PICO specification, search-strategy approval flow, exporters, and resumable runs, see the documentation.  
  
---  
  
## Export formats  
  
`--export FMT [FMT ...]` (or `to_*` exporters in [synthscholar.export](https://github.com/sensein/synthscholar/blob/main/synthscholar/export.py)):  
  
| Flag | Format | Use it for |  
|---|---|---|  
| `md`, `markdown` | Markdown | Human-readable narrative review with PRISMA flow, study tables, RoB, GRADE, synthesis, and per-study charting/appraisal sections. |  
| `json` | JSON | Full structured `PRISMAReviewResult` — every Pydantic field including evidence spans, charting rubrics, narrative rows, GRADE, grounding validation. |  
| `bib`, `bibtex` | BibTeX | Citation entries for every included article — drop into LaTeX or a reference manager. |  
| `ttl`, `turtle` | Turtle RDF | SLR (systematic literature review)-Ontology graph reusing PROV-O / FaBiO / BIBO / OA. Useful for KGs and SPARQL. |  
| `jsonld`, `json-ld` | JSON-LD | Same RDF graph in JSON-LD encoding. |  
  
Combine multiple in one run:  
  
```bash  
synthscholar --title "..." --export md json bib turtle jsonld
```  
  
To persist a queryable in-memory pyoxigraph store to disk:  
  
```bash  
synthscholar --title "..." --export turtle --rdf-store-path my_review.ttl
```  
  
Python:  
  
```python  
from synthscholar.export import to_markdown, to_json, to_bibtex, to_turtle, to_jsonld  
  
md  = to_markdown(result)  
js  = to_json(result)  
bib = to_bibtex(result)  
ttl = to_turtle(result)  
ld  = to_jsonld(result)  
```  
  
It also allows per-section exports (`to_charting_markdown`, `to_appraisal_json`, etc.) and compare-mode exporters (`to_compare_markdown`, `to_compare_json`). For details please refer to the documentation API section.
  
---  
  
## Running in comparision mode
  
Run the same protocol across two-to-five LLMs in parallel to produce a field-level agreement report and individual report that you can compare. Article acquisition (PubMed/bioRxiv/medRxiv search, screening, full-text resolution) runs **once**; only the LLM-driven steps fan out per model.  
  
CLI:  
  
```bash  
synthscholar \
  --title "GLP-1 agonists for type 2 diabetes" \
  --compare-models anthropic/claude-sonnet-4 openai/gpt-4o google/gemini-2.5-pro \
  --auto \
  --export md json
```  
  
Python:  
  
```python  
import asyncio  
from synthscholar.models import ReviewProtocol  
from synthscholar.pipeline import PRISMAReviewPipeline  
from synthscholar.compare import run_compare  
  
protocol = ReviewProtocol(question="...", pico_intervention="...", inclusion_criteria="...")  
pipeline = PRISMAReviewPipeline(api_key="<openrouter-key>", protocol=protocol)  
  
result = asyncio.run(run_compare(  
 pipeline=pipeline, models=[ "anthropic/claude-sonnet-4", "openai/gpt-4o", "google/gemini-2.5-pro", ],))  
  
# Per-model results + cross-model agreement metrics  
for model, sub in result.results.items():  
 print(model, len(sub.included_articles), "included")print(result.field_agreement)  
```  

---  
  
## FastAPI integration  
  
Four patterns are supported out of the box: **server-sent events** for live progress, a **plan-confirmation callback** for human-in-the-loop search-strategy approval, a **per-disorder synthesis** endpoint that re-buckets a finished review by `disorder_cohort`, and a **per-database PRISMA flow**.  
  
### Server-sent events (progress streaming)  
  
```python  
import asyncio
import json
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from synthscholar.models import ReviewProtocol
from synthscholar.pipeline import PRISMAReviewPipeline

app = FastAPI()

@app.post("/review/stream")
async def stream_review(protocol: ReviewProtocol):
    queue: asyncio.Queue[str] = asyncio.Queue()

    def on_progress(msg: str) -> None:
        queue.put_nowait(msg)

    pipeline = PRISMAReviewPipeline(
        api_key="<openrouter-key>",
        protocol=protocol
    )

    async def events():
        task = asyncio.create_task(
            pipeline.run(
                progress_callback=on_progress,
                auto_confirm=True
            )
        )

        while not task.done() or not queue.empty():
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield f"data: {json.dumps({'progress': msg})}\n\n"
            except asyncio.TimeoutError:
                continue

        result = await task
        yield f"data: {json.dumps({'done': True, 'synthesis': result.synthesis_text})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
```  
  
The frontend consumes with the standard [`EventSource`](https://developer.mozilla.org/en-US/docs/Web/API/EventSource) API.  
  
### Human-in-the-loop for plan-confirmation 
  
After the search-strategy agent generates a `ReviewPlan`, the pipeline hands it to your `confirm_callback`. Return `True` to accept, `False` to abort, or a string with feedback to regenerate the plan (up to `max_plan_iterations`).  
  
```python  
async def confirm_strategy(plan):
    # Send the plan to your UI/queue, await operator approval, return decision.
    decision = await ui_client.request_plan_approval(plan)

    if decision == "approved":
        return True
    if decision == "rejected":
        return False

    # str → treated as feedback; LLM regenerates the plan
    return decision


result = await pipeline.run(
    progress_callback=on_progress,
    confirm_callback=confirm_strategy,
)  
)  
```  
  
In a non-interactive environment without a callback, the pipeline runs in the auto mode.  
  
### Per-disorder synthesis endpoint  
  
Re-synthesise a finished review's included corpus, one bucket per distinct disorder. Useful when the protocol spans multiple cohorts and the UI wants strict, reproducible strata rather than the LLM-chosen grouping that `run_search_synthesis` produces.  
  
```python  
from synthscholar.agents import (
    AgentDeps, disorder_labels_from_rubrics, run_per_disorder_synthesis,
)

@app.post("/review/{review_id}/per-disorder-synthesis")
async def per_disorder_synthesis(review_id: str, min_per_disorder: int = 1):
    result = REVIEW_RESULTS[review_id]                       # your storage
    labels = disorder_labels_from_rubrics(result.data_charting_rubrics)
    deps = AgentDeps(
        protocol=result.protocol,
        api_key=os.environ["OPENROUTER_API_KEY"],
    )
    synth = await run_per_disorder_synthesis(
        result.included_articles, labels, deps,
        topic=result.research_question,
        min_articles_per_disorder=min_per_disorder,
    )
    return synth.model_dump()
```  
  
Buckets run in parallel — latency scales with the largest bucket plus one round-trip, not with the number of disorders. Articles whose rubric carries no `disorder_cohort` are excluded from synthesis and counted in `unlabeled_count` for transparency.  
  
### Displaying per-database PRISMA numbers  
  
Every result carries the **per-database identification numbers**. Expose them via a thin endpoint so the UI can render the flow diagram without re-tallying:  
  
```python  
@app.get("/review/{review_id}/prisma-flow")
async def prisma_flow(review_id: str):
    f = REVIEW_RESULTS[review_id].flow

    return {
        "db_pubmed": f.db_pubmed,
        "db_biorxiv": f.db_biorxiv,
        "db_medrxiv": f.db_medrxiv,
        "db_related": f.db_related,
        "db_hops": f.db_hops,
        "db_other_sources": f.db_other_sources,  # dict[str, int] — OA providers

        "total_identified": f.total_identified,
        "duplicates_removed": f.duplicates_removed,

        "screened": f.screened_title_abstract,
        "excluded_screening": f.excluded_title_abstract,

        "full_text_assessed": f.assessed_eligibility,
        "excluded_eligibility": f.excluded_eligibility,
        "excluded_reasons": f.excluded_reasons,  # dict[str, int]

        "included": f.included_synthesis,
    }
 ```  
---  
  
## UI integration  
  
You can connect your backend (FastAPI) to any frontend — React, Vue, Svelte, or even plain JavaScript.

The code below uses something called EventSource, which listens to a stream of updates from your server.
  
```js  
const es = new EventSource('/review/stream', {
  withCredentials: true,
});

es.onmessage = (event) => {
  const data = JSON.parse(event.data);

  if (data.progress) {
    updateProgressUI(data.progress);
  }

  if (data.done) {
    showSynthesis(data.synthesis);
    es.close();
  }
}; 
```  
  
Start a review with `/review/start` to get a `review_id`, display the plan, then send the user’s decision (`approved`, `rejected`, or feedback) to `/plans/{review_id}/decision`. The backend waits at `confirm_callback` and resumes once a decision is received.
  
Once a review is finished, you can render the secondary tabs by calling the read-only endpoints:  
  
```js  
// --- PRISMA 2020 Flow Diagram  ---
const flow = await fetch(`/review/${id}/prisma-flow`)
  .then((r) => r.json());

renderFlow({
  pubmed: flow.db_pubmed,
  biorxiv: flow.db_biorxiv,
  medrxiv: flow.db_medrxiv, // now separated (was previously grouped)
  related: flow.db_related,
  hops: flow.db_hops,
  other: flow.db_other_sources, // { openalex: 12, europepmc: 4, ... }

  total: flow.total_identified,
  included: flow.included,
});


// --- Per-Disorder Synthesis ---
const strata = await fetch(
  `/review/${id}/per-disorder-synthesis`,
  {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      review_id: id,
      min_articles_per_disorder: 2,
    }),
  }
).then((r) => r.json());

renderDisorderStrata({
  disorders: strata.groups, // [{ label, n_studies, aggregate_finding, representative_pmids }]
  unlabeled: strata.unlabeled_count,
});
```  
  
---  
  
## Caching and resumable runs  
  
PostgreSQL caching gives you three things on top of the default in-process SQLite article cache:  
  
- Cache full `PRISMAReviewResult`s keyed by **protocol similarity** (default ≥ 95 %); near-duplicate protocols return in seconds instead of minutes.  
- Persist **checkpoints** between major pipeline stages so a long run interrupted by a network error or rate-limit can be resumed in place.  
- Index every fetched article in a separate `ArticleStore` so future reviews on overlapping topics skip the PubMed/bioRxiv re-fetch.  
  
Run the migrations once against your DSN before first use:  
  
```bash  
# Run database migrations (in order)
psql "$PRISMA_PG_DSN" -f synthscholar/cache/migrations/001_initial.sql
psql "$PRISMA_PG_DSN" -f synthscholar/cache/migrations/002_add_sharing.sql
psql "$PRISMA_PG_DSN" -f synthscholar/cache/migrations/003_add_pipeline_checkpoints.sql
```  
  
### CLI  
  
```bash  
# Set database connection
export PRISMA_PG_DSN="postgresql://localhost/synthscholar"

# Run (second run with same/near-duplicate protocol will hit cache)
synthscholar --title "..." --pg-dsn "$PRISMA_PG_DSN"
synthscholar --title "..." --pg-dsn "$PRISMA_PG_DSN"

# Force fresh run (bypass cache and overwrite entry)
synthscholar --title "..." --pg-dsn "$PRISMA_PG_DSN" --force-refresh

# Tune cache similarity + expiration
synthscholar \
  --title "..." \
  --pg-dsn "$PRISMA_PG_DSN" \
  --cache-threshold 0.90 \
  --cache-ttl-days 60
```  
  
`--pg-dsn` defaults to the `PRISMA_PG_DSN` environment variable if not provided. The `--no-cache` flag only disables the temporary SQLite cache used during a single run—it does not affect Postgres caching.
  
### Python  
  
```python  
from synthscholar.models import ReviewProtocol
from synthscholar.pipeline import PRISMAReviewPipeline

protocol = ReviewProtocol(
    question="...",
    pg_dsn="postgresql://localhost/synthscholar",  # enable Postgres cache

    force_refresh=False,     # True → bypass cache and overwrite
    cache_threshold=0.95,    # similarity threshold for cache hit (0–1)
    cache_ttl_days=30,       # 0 = never expire
)

pipeline = PRISMAReviewPipeline(
    api_key="...",
    protocol=protocol,
)

result = await pipeline.run()

# Check if cache was used
# result.cache_hit → bool
```  
  
---  
  
## Search the past reviews  
  
Once Postgres caching is enabled, all fetched articles (including full text when available) and completed reviews become searchable. You can query across two corpora (articles and review cache) using three modes: default lexical full-text search (FTS), title-boosted lexical search, or semantic search.
  
### Migrations  
  
Run the optional FTS / pgvector migration once:  
  
```bash  
psql "$PRISMA_PG_DSN" -f synthscholar/cache/migrations/004_add_embeddings.sql
```  
  
Migration 004 adds:  
  
- `embedding VECTOR(384)` columns + IVF flat indexes on both `article_store` and `review_cache`  
- a `search_vector TSVECTOR` (with GIN index) on `review_cache` (article_store already had one in migration 001)  
  
For semantic search, also install the optional dependency:  
  
```bash  
pip install "synthscholar[semantic]"   # adds sentence-transformers (~80 MB model, CPU-friendly)
```  

If you would install with fulltext, do the following.

```bash
pip install "synthscholar[fulltext,semantic]"
```
  
### CLI  
  
The `synthscholar-search` console script ships with two subcommands:  
  
```bash  
# Lexical FTS over title + abstract + full text (no LLM cost, default)  
synthscholar-search literature "GLP-1 obesity adolescents" --top 15  
  
# Title-favouring lexical  
synthscholar-search literature "diagnostic accuracy speech" --by-title --top 10  
  
# Semantic search via pgvector (needs migration 004 + [semantic] extra)  
synthscholar-search literature "depression screening conversational AI" --semantic --top 20  
  
# Same modes for the review cache (past reviews you've already run)  
synthscholar-search reviews "obesity treatment" --semantic --top 5  
  
# Add --summarize to feed the top-K results through the search-synthesis  
# agent and get a stratified summary (e.g. by condition / population / design)  
synthscholar-search literature "diagnostic accuracy AI" \  
 --semantic --top 25 --summarize --summary-top 15  
```  
  
The `--summarize` mode uses a single LLM call to generate a structured `SearchSynthesis` (overview, groups, and caveats). Output is human-readable by default; use `--json` for machine-readable output. Set `OPENROUTER_API_KEY` (or pass `--api-key`) when using `--summarize`.
  
### Python  
  
```python  
from synthscholar.cache.article_store import ArticleStore
from synthscholar.agents import AgentDeps, run_search_synthesis
from synthscholar.models import ReviewProtocol

# Connect to the article store (Postgres-backed)
store = ArticleStore(dsn="postgresql://localhost/synthscholar")
await store.connect()

# Run a semantic search over stored articles
articles = await store.search_semantic(
    "CRISPR sickle cell adolescents",
    limit=20,
)

# Prepare LLM dependencies
deps = AgentDeps(
    protocol=ReviewProtocol(question="..."),
    api_key="<openrouter-key>",
)

# Generate a structured synthesis
synthesis = await run_search_synthesis(
    query="CRISPR sickle cell adolescents",
    articles=articles,
    deps=deps,
    top_k=15,
)

# Inspect grouped results
for group in synthesis.groups:
    print(group.label, group.n_studies, group.aggregate_finding)

# Close connection
await store.close()
```  
  
### Integrating into your own FastAPI/UI app  
  
`synthscholar` is a library, not a hosted service. It provides the search engine (`ArticleStore`, `CacheStore`, and `run_search_synthesis`), which you can wire into your own FastAPI, Flask, or Litestar app just like the review pipeline. 

Below is the minimal example:
  
```python  
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from synthscholar.cache.article_store import ArticleStore
from synthscholar.agents import AgentDeps, run_search_synthesis
from synthscholar.models import ReviewProtocol

app = FastAPI()


class SearchRequest(BaseModel):
    query: str
    mode: str = "keyword"   # "keyword" | "by_title" | "semantic"
    top: int = 20
    summarize: bool = False
    summary_top: int = 15


@app.post("/search/literature")
async def search_literature(req: SearchRequest):
    store = ArticleStore(dsn="postgresql://localhost/synthscholar")
    await store.connect()

    try:
        # --- Search ---
        try:
            if req.mode == "semantic":
                arts = await store.search_semantic(req.query, limit=req.top)
            elif req.mode == "by_title":
                arts = await store.search_by_title(req.query, limit=req.top)
            else:
                arts = await store.search_by_keyword(req.query, limit=req.top)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))

        result = {
            "results": [a.model_dump() for a in arts]
        }

        # --- Optional LLM synthesis ---
        if req.summarize and arts:
            deps = AgentDeps(
                protocol=ReviewProtocol(question=req.query),
                api_key="<openrouter-key>",
            )

            synth = await run_search_synthesis(
                req.query,
                arts,
                deps,
                top_k=req.summary_top,
            )

            result["synthesis"] = synth.model_dump()

        return result

    finally:
        await store.close() 
```  
  
Use the same pattern for past-review search—just replace `ArticleStore` with `CacheStore` and call `search_reviews_keyword` or `search_reviews_semantic`.
  
For production you'll want connection pooling reuse rather than per-request connect/close, request-level deps for credentials, and SSE for the synthesis call.  
  
### What `--summarize` produces  
  
The agent auto-detects the most informative grouping dimension (typically condition / disorder when results span multiple disease areas) and returns:  
  
```json  
{
  "query": "diagnostic accuracy speech",
  "n_articles_synthesized": 14,

  "overview": "Cross-sectional accuracy studies (2019–2024), with sample sizes ranging from 30 to 820.",

  "groups": [
    {
      "label": "Parkinson's disease",
      "n_studies": 6,
      "aggregate_finding": "AUC 0.86 (range 0.78–0.94) on hold-out test sets.",
      "representative_pmids": ["...", "..."]
    },
    {
      "label": "Major depressive disorder",
      "n_studies": 5,
      "aggregate_finding": "Sensitivity 0.79, specificity 0.81.",
      "representative_pmids": ["..."]
    },
    {
      "label": "Alzheimer's / MCI",
      "n_studies": 3,
      "aggregate_finding": "AUC 0.83 (n=512).",
      "representative_pmids": ["..."]
    }
  ],

  "overall_caveats": "Heterogeneous reference standards and limited external validation cohorts."
}
```  
   
  
### Strict per-disorder synthesis  
  
`run_search_synthesis` lets the LLM choose how to group results. For deterministic, reproducible groups — one bucket per disorder — use `run_per_disorder_synthesis`. Articles are grouped using labels you provide, usually from `DataChartingRubric.disorder_cohort` after charting, and each group gets one LLM summary call.

```python  
from synthscholar.agents import (
    AgentDeps,
    disorder_labels_from_rubrics,
    run_per_disorder_synthesis,
)

# Derive disorder labels from charted rubrics (after a review run)
labels = disorder_labels_from_rubrics(
    result.data_charting_rubrics
)

# Set up LLM dependencies
deps = AgentDeps(
    protocol=result.protocol,
    api_key="<openrouter-key>",
)

# Run per-disorder synthesis
synth = await run_per_disorder_synthesis(
    result.included_articles,
    labels,
    deps,
    topic=result.research_question,
    min_articles_per_disorder=2,  # skip single-study groups
)

# Inspect results
print(f"{synth.n_disorders} disorders, {synth.unlabeled_count} articles unlabeled")

for group in synth.groups:
    print(
        f"  {group.label}: {group.n_studies} studies — "
        f"{group.aggregate_finding[:80]}…"
    )
```  
  
Bucketing is case-insensitive and whitespace-collapsed but the original casing is preserved on the `GroupSummary.label`. Articles missing from `disorder_labels` are excluded from synthesis and counted in `unlabeled_count` for transparency. Per-bucket calls run in parallel via `asyncio.gather`.  
  
---  

 
  
## Limitations  
  
SynthScholar automates a literature review workflow but is not a substitute for expert human judgment. Database coverage gaps (no Cochrane CENTRAL / EMBASE / Scopus), paywall fallback behaviour, Cloudflare-challenged bioRxiv direct PDFs, and the cost implications of parallel map-reduce synthesis on large corpora are some of the known limitations.
  
---  
  
## Citation  
  
If you use SynthScholar in academic work, please cite the project: <https://github.com/sensein/synthscholar>.  
  
---  
  
## License  
  
[Apache 2.0](https://github.com/sensein/synthscholar/blob/main/LICENSE).