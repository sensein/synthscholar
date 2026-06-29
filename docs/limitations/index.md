---
myst:
  html_meta:
    description: "Known limitations and operational caveats for SynthScholar"
---

# Known Limitations

SynthScholar automates a rigorous review workflow but is not a substitute for expert human judgment. The constraints below are intrinsic to the current implementation. Where a limit is configurable, the relevant parameter or code location is cited.

## Search and retrieval

- **Database coverage** — built-in search providers (in `synthscholar/clients.py`) cover **PubMed**, **bioRxiv**, **medRxiv**, **Europe PMC**, **OpenAlex**, **CrossRef**, **DOAJ**, **Semantic Scholar**, **arXiv**, **CORE**, and **Unpaywall** (the last as a DOI resolver). The default pipeline queries PubMed + bioRxiv + medRxiv; the others are available via `OAFetcher.search_all` but are not yet wired into the search-strategy agent. **Cochrane CENTRAL, EMBASE, Scopus, Web of Science, CINAHL, PsycINFO**, and most grey literature remain unsupported and must be supplemented manually.
- **Language** — search prompts and downstream LLM agents operate in English. There is no explicit language filter on PubMed queries, but non-English titles/abstracts may be screened inconsistently.
- **Per-query result cap** — PubMed search defaults to `max_per_query=20` results per query (`synthscholar/pipeline.py:387`). bioRxiv search is hard-coded to 10 results per query (`synthscholar/pipeline.py:594`). High-recall reviews must raise these explicitly.
- **bioRxiv lookback window** — defaults to 180 days (`biorxiv_days=180`). Older preprints are not retrieved unless the parameter is increased.
- **Related-articles seeds** — only the first **8** PubMed PMIDs from the initial search seed the `elink` neighbor expansion (`synthscholar/pipeline.py:611`).
- **Citation hops** — multi-hop navigation seeds from only the first **5** PMIDs, capped at 8 backward + 8 forward links per hop. Deeper or wider citation graphs are not explored.
- **Rate limits** — PubMed E-utilities calls use blocking `time.sleep(RATE_LIMIT_DELAY)` (`synthscholar/clients.py:103,109`). Throughput is capped at 3 req/s (10 req/s with `NCBI_API_KEY`).

## Full-text access

- **Open-access only** — full text is retrieved automatically when at least one open-access route exists. The pipeline tries (in order): PMC OA full text → Europe PMC OA full text → bioRxiv/medRxiv direct PDF → DOI resolver chain (Unpaywall → OpenAlex → Semantic Scholar) → PyMuPDF parse of any discovered PDF. Articles with no OA copy on any of these resolvers degrade to **abstract-only** processing.
- **PDF parsing requires `pymupdf`** — when the resolver finds a PDF URL (via Unpaywall / OpenAlex / Semantic Scholar / bioRxiv), it downloads the file and parses it with [PyMuPDF](https://pymupdf.readthedocs.io/). Install with `pip install "synthscholar[fulltext]"`. Without it, the resolver still works for Europe PMC's OA full-text-XML route but cannot extract from PDF URLs.
- **PDF extraction is plain text** — PyMuPDF flattens table structure, equations, and figure captions into linear text. Adequate for LLM consumption but not lossless. (Marker-pdf would preserve more structure but pins ``anthropic<0.47``, which is incompatible with this project's `pydantic-ai` dependency, so PyMuPDF is used instead.)
- **bioRxiv / medRxiv PDFs may be Cloudflare-challenged** — `www.biorxiv.org` and `www.medrxiv.org` front Cloudflare and may serve a bot-protection challenge (HTTP 403/503 or an HTML page) to non-browser clients, especially under bulk requests. The resolver follows the recommended polite-pool pattern: fetch the latest version number from `api.biorxiv.org/details/{server}/{DOI}/na/json` first, build the canonical `{doi}v{N}.full.pdf` URL, and validate the response (status, `Content-Type`, and the `%PDF-` magic bytes) before parsing. The resolver does **not** attempt to bypass Cloudflare — when a challenge is served, the article degrades to the next leg in the chain (Europe PMC OA-XML when available, else abstract-only). Europe PMC indexes bioRxiv/medRxiv preprints but provides full-text XML for only a subset.
- **No paywalled-publisher resolvers** — institutional proxies (SFX/OpenURL), publisher TDM APIs (Elsevier, Wiley, Springer), and shadow libraries are **not** integrated. Paywalled DOIs without an OA copy on Unpaywall/OpenAlex/Semantic Scholar remain abstract-only.

## LLM-driven steps and corpus handling

Synthesis-style agents process the **full** included corpus, not a prefix. When the corpus exceeds a per-call char budget, articles are sharded into batches that run in parallel and are then merged:

| Agent | Strategy | Budget / merge | Source |
|---|---|---|---|
| Synthesis | Single call ≤ budget; otherwise parallel partials → LLM merge | `SYNTHESIS_BATCH_CHARS = 80_000` chars; merged via `run_synthesis_merge_agent` | `synthscholar/agents.py:run_synthesis` |
| Thematic synthesis | Single call ≤ budget; otherwise parallel partials → deterministic structured merge | `THEMATIC_BATCH_CHARS = 60_000` chars; merged via `_merge_thematic_results` (themes deduped, paragraphs concatenated, risk-level promoted to most severe) | `synthscholar/agents.py:run_thematic_synthesis` |
| Bias summary, GRADE, Limitations | Always single call (per-article footprint is one short line) | No cap | `synthscholar/agents.py` |

What this means in practice:

- **Cost scales with corpus size.** A 200-article review will issue ≥ 2 synthesis batch calls plus a merge call instead of one synthesis call. Wall-clock stays roughly constant (batches run concurrently); token cost scales linearly.
- **Synthesis merging is LLM-mediated.** The unstructured-narrative merge can introduce wording drift between batches; cross-batch findings are reconciled by the merge agent, not by string concatenation.
- **Thematic merging is deterministic.** Themes with the same name are deduped and their `supporting_studies`/`key_findings` lists are unioned; `risk_level` is promoted to the most severe value across batches.
- **GRADE outcomes** — runs for **every** PICO outcome the protocol defines, with all outcomes assessed concurrently in one `asyncio.gather` round-trip. Cost scales linearly with outcome count; wall-clock stays roughly constant.
- **Non-determinism** — LLM sampling is not pinned to a fixed seed. Re-running the same protocol can produce different syntheses, narrative wording, and edge-case screening decisions.
- **Single LLM provider** — all agents route through OpenRouter. Direct Anthropic/OpenAI/Gemini SDK calls are not supported.
- **Default model** — `anthropic/claude-sonnet-4`. Smaller or older models may degrade typed-output reliability and grounding.
- **API-key and contact-email auth via env vars** — five environment variables are read automatically (precedence: explicit constructor argument > env var > built-in default):

  | Env var | Read by | Required? | What it unlocks |
  |---|---|---|---|
  | `OPENROUTER_API_KEY` | LLM agents | **yes** | All agent calls go through OpenRouter. No Python override. |
  | `NCBI_API_KEY` | `PRISMAReviewPipeline(ncbi_api_key=...)` | no | Lifts PubMed E-utilities rate limit 3 → 10 req/s. Faster search and citation hops. |
  | `SYNTHSCHOLAR_EMAIL` | `PRISMAReviewPipeline(email=...)`, `OAFetcher` / `FullTextResolver` | recommended | Polite-pool contact in `User-Agent` on every OA HTTP call and the `email=` parameter on Unpaywall (which **requires** it by ToS). |
  | `SEMANTIC_SCHOLAR_API_KEY` | `OAFetcher` / `FullTextResolver` (`api_keys={"semantic_scholar": ...}` to override) | no | Without a key, Semantic Scholar rate-limits at ~1 req/s — the bottleneck on the DOI-resolver chain for large reviews. With a (free) key, full-text resolution for 200-article reviews finishes in minutes instead of hours. |
  | `CORE_API_KEY` | `OAFetcher` (`api_keys={"core": ...}` to override) | no, but provider is dead without it | CORE has **no public anonymous tier** — without a key the CORE provider is silently skipped during multi-source search. Setting it adds a worldwide OA-repository discovery leg to `OAFetcher.search_all()`. |

  **Why both Semantic Scholar and CORE?** They serve different roles in the pipeline. Semantic Scholar is a **DOI resolver** in the full-text chain (`Unpaywall → OpenAlex → Semantic Scholar → PDF download`) — its `openAccessPdf.url` field is often what unlocks full text for articles missing from PMC and Europe PMC. CORE is a **discovery source** that widens the initial search across thousands of OA repositories beyond PubMed/bioRxiv. The pipeline is productive without either; supplying Semantic Scholar's key improves throughput, supplying CORE's expands coverage. Setting both is recommended for high-recall reviews.

  **CLI users:** every env var above has a matching `--flag` (precedence: CLI flag > env var > default):

  | Env var | CLI flag |
  |---|---|
  | `OPENROUTER_API_KEY` | `--api-key` |
  | `NCBI_API_KEY` | `--ncbi-api-key` |
  | `SYNTHSCHOLAR_EMAIL` | `--email` |
  | `SEMANTIC_SCHOLAR_API_KEY` | `--semantic-scholar-key` |
  | `CORE_API_KEY` | `--core-key` |

  Python users can pass everything explicitly via `PRISMAReviewPipeline(api_key=..., ncbi_api_key=..., email=..., api_keys={"semantic_scholar": ..., "core": ...})`.

## Source grounding

- **Lexical, not semantic** — the grounding gate uses `rapidfuzz` (`partial_ratio` ∨ `token_set_ratio`, threshold = **65/100** by default; see `synthscholar/validation.py:21-24,48`). Legitimate paraphrases that diverge significantly in surface form may be dropped; fabrications that happen to share lexical overlap may pass.
- **Short-span unreliability** — spans below `MIN_VERIFIABLE_TOKENS = 4` produce unstable partial-match scores (`synthscholar/validation.py:51`).
- **Silent disable** — if `rapidfuzz` is not installed, grounding validation is **silently disabled** with a warning (`synthscholar/validation.py:42-45`).

## Screening, RoB, and appraisal

- **AI-assisted screening** — both stages (title/abstract, full-text) are LLM-driven. PRISMA 2020 dual-reviewer consensus is **not** simulated; runs should be reviewed by a human screener for high-stakes reviews.
- **RoB tool selection** — fixed at protocol time (RoB 2, ROBINS-I, Newcastle-Ottawa, or QUADAS-2). The pipeline does not auto-detect study design and switch tools per article.
- **RoB inputs** — abstracts only when full text is unavailable (see *Full-text access*); per-article judgments may be over-confident given limited context.
- **Critical appraisal** — operates on the data-charting rubric, not the raw article. Errors in charting propagate.

## Output and synthesis

- **Narrative-first** — meta-analytic effect-size pooling is limited to what the `quantitative_analysis_agent` extracts; no fixed/random-effects model fitting, forest plots, or heterogeneity statistics are computed.
- **No PRISMA flow diagram image** — flow counts are produced; the visual diagram is not rendered.
- **English-only output** — all generated sections (abstract, introduction, discussion, conclusions) are English.

## Caching and reproducibility

- **Similarity-keyed cache** — protocols matching at ≥ 95% are served from cache. Subtle protocol differences below this threshold can return a stale review unless `--no-cache` or a force flag is set.
- **PostgreSQL only** — the production cache is PostgreSQL ≥ 15. SQLite is used for tests but is not a supported deployment target.
- **No retraction tracking** — the article store does not flag retracted publications discovered after caching.

## Operational

- **Single-machine async** — concurrency is controlled by `--concurrency` (default 5). There is no distributed job queue or multi-host scheduler.
- **No incremental updates** — re-running the same protocol re-screens all articles (subject to cache hits). There is no "show me only new evidence since date X" mode.
- **No post-hoc revision** — once a review completes, edits to inclusion criteria, RoB judgments, or syntheses must be made by re-running with an updated protocol; there is no interactive editor.
- **Compare mode non-determinism** — multi-LLM compare runs amplify the non-determinism above; consensus synthesis is itself an LLM step.

## Scope this tool does not claim to cover

- Living systematic reviews with continuous evidence surveillance.
- Diagnostic test accuracy meta-analysis (bivariate / HSROC models).
- Network meta-analysis or indirect treatment comparisons.
- Individual-patient-data (IPD) meta-analysis.
- Qualitative evidence synthesis frameworks (e.g., GRADE-CERQual, meta-ethnography) beyond narrative thematic synthesis.
