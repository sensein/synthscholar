# SynthScholar — PRISMA 2020 Systematic Review Agent

A multi-agent system for automated, PRISMA-2020-guided systematic literature reviews. Built on [pydantic-ai](https://ai.pydantic.dev/) for typed agent outputs and routed through [OpenRouter](https://openrouter.ai/), so any frontier LLM (Claude, GPT, Gemini, DeepSeek, ...) can drive the same pipeline.

**[Documentation](https://github.com/sensein/synthscholar/tree/main/docs)** · [Source](https://github.com/sensein/synthscholar) · [Issues](https://github.com/sensein/synthscholar/issues)

---

## Install

```bash
pip install synthscholar                  # core
pip install "synthscholar[fulltext]"      # adds PDF parsing (PyMuPDF) + arXiv (feedparser)
```

From source:

```bash
git clone https://github.com/sensein/synthscholar.git
cd synthscholar
uv sync --extra fulltext                  # or: pip install -e ".[fulltext]"
```

Requires Python 3.11 or newer. PostgreSQL 15+ is optional and only needed for the protocol-similarity cache and resumable-run checkpoints.

---

## Quick start

### 1. Set your API keys

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."        # required — drives every LLM agent
export NCBI_API_KEY="..."                       # optional — raises PubMed rate limits 3 → 10 req/s
export SYNTHSCHOLAR_EMAIL="you@example.com"     # optional — polite-pool contact for OA providers (Unpaywall requires it)
export SEMANTIC_SCHOLAR_API_KEY="..."           # optional — higher Semantic Scholar rate limits + OA PDF lookups
export CORE_API_KEY="..."                       # optional — enables the CORE OA aggregator (no-op if unset)
```

All five are read automatically. **Precedence: explicit constructor argument > env var > built-in default.**

- `OPENROUTER_API_KEY` — required. Used by every LLM agent. There is no Python override; the agents read it from the environment.
- `NCBI_API_KEY` — optional. Lifts the PubMed E-utilities rate limit from 3 → 10 req/s, so search and citation-hop steps finish ~3× faster. Without it the pipeline still works; large reviews just take longer. Pass via `PRISMAReviewPipeline(ncbi_api_key=...)` to override.
- `SYNTHSCHOLAR_EMAIL` — optional but recommended. Becomes the polite-pool contact in the `User-Agent` header on every OA HTTP call and the `email=` parameter on Unpaywall (which requires it by ToS). Pass via `PRISMAReviewPipeline(email=...)`.
- `SEMANTIC_SCHOLAR_API_KEY` — optional. Without a key, Semantic Scholar's public tier rate-limits to ~1 req/s, which becomes the bottleneck on the DOI-resolver chain (Unpaywall → OpenAlex → **Semantic Scholar** → PDF download) for reviews with many included articles. With a free key, rate limits jump enough that a 200-article review's full-text resolution finishes in minutes instead of hours. Pass `api_keys={"semantic_scholar": "..."}` to `OAFetcher()` / `FullTextResolver()` to override.
- `CORE_API_KEY` — optional. CORE is a world-wide OA aggregator that **requires a key** — no public anonymous tier. Without it the CORE provider is silently skipped during multi-source search; setting a key is the only way to enable that leg of `OAFetcher.search_all()`. Pass `api_keys={"core": "..."}` to override.

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

- `--compare-models claude-sonnet-4 gpt-4o gemini-2.5-pro` — multi-LLM compare mode.
- `--auto` — skip the interactive search-strategy confirmation (good for scripts/CI).
- `--no-cache` — bypass the protocol-similarity cache and re-run from scratch.
- `--max-results N`, `--related-depth N`, `--hops N` — tune search breadth.
- `--export md json turtle jsonld` — pick output formats.

Run `synthscholar --help` for the full list, or see the [CLI reference](https://github.com/sensein/synthscholar/blob/main/docs/cli.md).

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

In practice you'll usually set the env vars once (via `.env` or your shell rc) and skip the flags; the flags are useful for one-off overrides, CI matrices, or environments where you don't want to export secrets.

### 3. Run a review (Python)

```python
import asyncio
from synthscholar.models import ReviewProtocol
from synthscholar.pipeline import PRISMAReviewPipeline

protocol = ReviewProtocol(
    question="What is the efficacy of CRISPR-based therapies for sickle cell disease?",
    pico_population="patients with sickle cell disease",
    pico_intervention="CRISPR gene therapy",
    pico_outcome="haematologic response, transfusion independence",
    inclusion_criteria="Clinical trials in humans, English-language",
    databases=["PubMed", "bioRxiv", "medRxiv"],
)

pipeline = PRISMAReviewPipeline(
    api_key="<openrouter-key>",          # required (or OPENROUTER_API_KEY env var, read by agents)
    ncbi_api_key="<ncbi-key>",           # or rely on NCBI_API_KEY env var
    email="you@example.com",             # or rely on SYNTHSCHOLAR_EMAIL env var
    api_keys={                            # or rely on SEMANTIC_SCHOLAR_API_KEY / CORE_API_KEY env vars
        "semantic_scholar": "<s2-key>",
        "core": "<core-key>",
    },
    protocol=protocol,
)
result = asyncio.run(pipeline.run())
print(result.synthesis_text)
```

Every auth field falls back to its env var when omitted, so the same call works in dev (everything in env) and in production (explicit dict from a secret manager).

For full PICO specification, search-strategy approval flow, exporters, and resumable runs, see the [Quick Start guide](https://github.com/sensein/synthscholar/blob/main/docs/quickstart.md).

---

## Export formats

`--export FMT [FMT ...]` (or `to_*` exporters in [synthscholar.export](https://github.com/sensein/synthscholar/blob/main/synthscholar/export.py)):

| Flag | Format | Use it for |
|---|---|---|
| `md`, `markdown` | Markdown | Human-readable narrative review with PRISMA flow, study tables, RoB, GRADE, synthesis, and per-study charting/appraisal sections. |
| `json` | JSON | Full structured `PRISMAReviewResult` — every Pydantic field including evidence spans, charting rubrics, narrative rows, GRADE, grounding validation. |
| `bib`, `bibtex` | BibTeX | Citation entries for every included article — drop into LaTeX or a reference manager. |
| `ttl`, `turtle` | Turtle RDF | SLR-Ontology graph reusing PROV-O / FaBiO / BIBO / OA. Useful for KGs and SPARQL. |
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

Granular per-section exporters (`to_charting_markdown`, `to_appraisal_json`, etc.) and compare-mode exporters (`to_compare_markdown`, `to_compare_json`) are available too — see the [API reference](https://github.com/sensein/synthscholar/tree/main/docs/api).

---

## Compare mode (multi-LLM)

Run the same protocol across two-to-five LLMs in parallel and produce a field-level agreement report. Article acquisition (PubMed/bioRxiv/medRxiv search, screening, full-text resolution) runs **once**; only the LLM-driven steps fan out per model — so you pay article-fetch costs once.

CLI:

```bash
synthscholar \
  --title "GLP-1 agonists for type 2 diabetes" \
  --compare-models anthropic/claude-sonnet-4 openai/gpt-4o google/gemini-2.5-pro \
  --auto --export md json
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
    pipeline=pipeline,
    models=[
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
        "google/gemini-2.5-pro",
    ],
))

# Per-model results + cross-model agreement metrics
for model, sub in result.results.items():
    print(model, len(sub.included_articles), "included")
print(result.field_agreement)
```

See [Compare mode guide](https://github.com/sensein/synthscholar/blob/main/docs/guides/compare-mode.md) for consensus synthesis, agreement-metric semantics, and recommended model panels.

---

## FastAPI integration

Two patterns are supported out of the box: **server-sent events** for live progress, and a **plan-confirmation callback** for human-in-the-loop search-strategy approval.

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

    pipeline = PRISMAReviewPipeline(api_key="<openrouter-key>", protocol=protocol)

    async def events():
        task = asyncio.create_task(
            pipeline.run(progress_callback=on_progress, auto_confirm=True)
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

The frontend consumes with the standard `EventSource` API.

### Plan-confirmation callback (human-in-the-loop)

After the search-strategy agent generates a `ReviewPlan`, the pipeline hands it to your `confirm_callback`. Return `True` to accept, `False` to abort, or a string with feedback to regenerate the plan (up to `max_plan_iterations`).

```python
async def confirm_strategy(plan):
    # Send the plan to your UI/queue, await operator approval, return decision.
    decision = await ui_client.request_plan_approval(plan)
    if decision == "approved":
        return True
    if decision == "rejected":
        return False
    return decision  # str → treated as feedback; LLM regenerates the plan

result = await pipeline.run(
    progress_callback=on_progress,
    confirm_callback=confirm_strategy,
)
```

In a non-interactive environment without a callback, the pipeline auto-confirms safely (logged) instead of blocking on stdin.

See [FastAPI guide](https://github.com/sensein/synthscholar/blob/main/docs/guides/fastapi.md) for a minimal complete app, polling fallback for environments without SSE, typed Python client, and production notes (timeouts, queue backpressure, error handling).

---

## UI integration

Drop the FastAPI endpoint above behind any frontend — React, Vue, Svelte, plain JS. The `EventSource` consumer is ~20 lines:

```js
const es = new EventSource('/review/stream', { withCredentials: true });

es.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.progress) updateProgressUI(data.progress);
  if (data.done) {
    showSynthesis(data.synthesis);
    es.close();
  }
};
```

For plan confirmation, pair `/review/start` (returns a `review_id`) with `/plans/{review_id}/decision` (POST `approved|rejected|<feedback>`) and have the FastAPI handler block the `confirm_callback` until the decision lands.

See [UI integration guide](https://github.com/sensein/synthscholar/blob/main/docs/guides/ui-integration.md) for a complete React example with progress bar, plan-review modal, and result rendering.

---

## Caching and resumable runs

Set `PRISMA_DB_DSN=postgresql://user:pass@host:5432/db` and the pipeline will:

- Cache full `PRISMAReviewResult`s keyed by **protocol similarity** (default ≥ 95 %); near-duplicate protocols return in seconds instead of minutes.
- Persist **checkpoints** between major pipeline stages so a long run interrupted by a network error or rate-limit can be resumed in place.
- Index every fetched article in a separate `ArticleStore` so future reviews on overlapping topics skip the PubMed/bioRxiv re-fetch.

```bash
export PRISMA_DB_DSN="postgresql://localhost/synthscholar"
synthscholar --title "..."                # cache hit on second run
synthscholar --title "..." --no-cache     # force fresh
```

See [Caching guide](https://github.com/sensein/synthscholar/blob/main/docs/guides/caching.md) for migration setup, similarity tuning, and checkpoint internals.

---

## What's included

- **PRISMA 2020 pipeline** — 18 async steps and 16 specialised pydantic-ai agents, from search-strategy generation through two-stage screening, evidence extraction, RoB, data charting, critical appraisal, narrative rows, grounded synthesis, GRADE (per outcome, run concurrently), and a structured abstract with PRISMA flow counts.
- **Multi-source OA discovery** — built-in providers for PubMed, bioRxiv, medRxiv, Europe PMC, OpenAlex, CrossRef, DOAJ, Semantic Scholar, arXiv, CORE, and Unpaywall.
- **Full-text resolver chain** — for each included article, tries PMC OA → Europe PMC OA-XML → preprint PDF (canonical `{doi}v{N}.full.pdf`, Cloudflare-aware) → DOI resolvers (Unpaywall → OpenAlex → Semantic Scholar) and parses any reachable PDF with [PyMuPDF](https://pymupdf.readthedocs.io/).
- **Source grounding** — every evidence span is fuzzy-matched back to its source article; ungrounded text is dropped.
- **Parallel map-reduce synthesis** — for corpora that exceed a per-call char budget, articles are sharded into char-budgeted batches that run in parallel; partials are merged via an LLM (synthesis) or deterministically (thematic synthesis).
- **Multi-model compare mode** — run one protocol across 2–5 LLMs in parallel and produce a field-level agreement report.
- **PostgreSQL cache + checkpoints** — similarity-keyed result cache and resumable runs for long reviews.
- **Multiple export formats** — Markdown, JSON, BibTeX, Turtle, JSON-LD (via the SLR Ontology, reusing PROV-O / FaBiO / BIBO / OA).
- **Configurable RoB tools** — RoB 2, ROBINS-I, Newcastle-Ottawa, QUADAS-2.

---

## Documentation

- [Installation](https://github.com/sensein/synthscholar/blob/main/docs/installation.md)
- [Quick start](https://github.com/sensein/synthscholar/blob/main/docs/quickstart.md)
- [Architecture & 18-step pipeline](https://github.com/sensein/synthscholar/blob/main/docs/architecture.md)
- [CLI reference](https://github.com/sensein/synthscholar/blob/main/docs/cli.md)
- [Compare mode](https://github.com/sensein/synthscholar/blob/main/docs/guides/compare-mode.md)
- [FastAPI integration](https://github.com/sensein/synthscholar/blob/main/docs/guides/fastapi.md)
- [UI integration](https://github.com/sensein/synthscholar/blob/main/docs/guides/ui-integration.md)
- [Caching & checkpoints](https://github.com/sensein/synthscholar/blob/main/docs/guides/caching.md)
- [API reference](https://github.com/sensein/synthscholar/tree/main/docs/api)
- [SLR ontology](https://github.com/sensein/synthscholar/blob/main/docs/ontology.md)
- [Known limitations](https://github.com/sensein/synthscholar/blob/main/docs/limitations/index.md)

---

## Limitations

SynthScholar automates a rigorous review workflow but is not a substitute for expert human judgment. Database coverage gaps (no Cochrane CENTRAL / EMBASE / Scopus), paywall fallback behaviour, Cloudflare-challenged bioRxiv direct PDFs, and the cost implications of parallel map-reduce synthesis on large corpora are documented on the [Known Limitations](https://github.com/sensein/synthscholar/blob/main/docs/limitations/index.md) page. Review it before relying on outputs for high-stakes decisions.

---

## Citation

If you use SynthScholar in academic work, please cite the project: <https://github.com/sensein/synthscholar>.

---

## License

[Apache 2.0](https://github.com/sensein/synthscholar/blob/main/LICENSE).
