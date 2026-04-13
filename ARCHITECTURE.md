# PRISMA Review Agent — System Architecture

> **Version:** 1.0.0 | **Author:** Tek Raj Chhetri | **License:** Apache 2.0

---

## 1. What PRISMA Review Agent Does

PRISMA Review Agent automates the entire systematic literature review workflow following the PRISMA 2020 guidelines. A researcher defines a review protocol — a research question framed as PICO (Population, Intervention, Comparison, Outcome), inclusion and exclusion criteria, and which risk-of-bias tool to use — and the system handles everything else: formulating search queries, retrieving literature from PubMed and bioRxiv, screening articles in two stages, extracting evidence, assessing risk of bias, and producing a complete PRISMA 2020 formatted report.

The system is fully domain-agnostic. It does not contain any hardcoded knowledge about any clinical area. Every piece of domain knowledge — the research question, the eligibility criteria, the outcomes of interest, the risk-of-bias domains — is injected at runtime from the `ReviewProtocol` object. The same codebase can conduct a systematic review of randomised controlled trials in cardiology, observational studies in neuroscience, or qualitative studies in education without any code changes.

---

## 2. System Overview

The system has ten conceptual stages that execute in sequence, with one parallel step:

```
ReviewProtocol (user input)
        |
        v
Agent 1: search_strategy_agent
        |  Generates PubMed + bioRxiv query strings
        v
Data Collection Tools  (PubMed, bioRxiv, elink)
        |  Retrieves articles, related, citations
        v
Deduplication  (by DOI / PMID)
        |
        v
Agent 2: screening_agent  [Stage A: Title/Abstract, INCLUSIVE bias]
        |  Include / Exclude decisions, batch 15
        v
PMC Full-text Fetch  (Tool)
        |  Populates article.full_text for eligible articles
        v
Agent 2: screening_agent  [Stage B: Full-text, STRICT bias]
        |  Final eligibility decisions, batch 10
        v
Article-level analysis (per included article):
  Agent 9: evidence_extraction_agent  (batch 5)
  Agent 4: data_extraction_agent      (per-article, optional)
  Agent 3: rob_agent                  (per-article)
        |
        v
asyncio.gather() — all run in parallel:
  Agent 5: synthesis_agent
  Agent 7: bias_summary_agent
  Agent 6: grade_agent  (per outcome)
  Agent 8: limitations_agent
        |
        v
Assemble PRISMAReviewResult
        |
        v
Export: Markdown (PRISMA 2020 report) / JSON / BibTeX
```

The codebase maps to this flow as follows:

```
prisma_review_agent/
  main.py       -- CLI entry point (argparse + interactive mode)
  pipeline.py   -- PRISMAReviewPipeline: 15-step async orchestrator
  agents.py     -- 9 pydantic-ai LLM agents + runner functions
  clients.py    -- PubMedClient + BioRxivClient + SQLite Cache
  models.py     -- All Pydantic v2 data models (15+ classes)
  evidence.py   -- Thin wrapper for evidence extraction runner
  export.py     -- Markdown / JSON / BibTeX formatters
```

---

## 3. Step-by-Step Flow in Natural Language

### Step 1 — Researcher defines a ReviewProtocol

Everything starts with a `ReviewProtocol` Pydantic object. The researcher provides this either through the CLI (`prisma-review --title "..." --population "..." ...`) or programmatically in Python. The protocol carries:

- **`title`** — the research question in full (e.g., "Effectiveness of BDNF-targeted therapies in major depressive disorder")
- **`objective`** — a more detailed statement of what the review aims to establish
- **`pico_population`**, **`pico_intervention`**, **`pico_comparison`**, **`pico_outcome`** — the four PICO components that structure the clinical question
- **`inclusion_criteria`** and **`exclusion_criteria`** — the eligibility rules that every article will be judged against during screening
- **`databases`** — which databases to search (default: PubMed and bioRxiv)
- **`rob_tool`** — which risk-of-bias assessment instrument to use; the system supports eleven tools including RoB 2, ROBINS-I, Newcastle-Ottawa Scale, QUADAS-2, CASP, JBI, and Jadad
- **`date_range_start`**, **`date_range_end`** — optional date restrictions for the search

This protocol object is passed to the `PRISMAReviewPipeline` constructor and will be injected as dynamic context into every LLM agent that runs throughout the pipeline. No agent hard-codes domain knowledge — they all read what they need from the protocol at call time.

---

### Step 2 — Agent 1: Search Strategy formulates database queries

The first thing the pipeline does is pass the `ReviewProtocol` to **Agent 1**, the `search_strategy_agent`. This is a pydantic-ai agent that reads the PICO components and eligibility criteria and produces a `SearchStrategy` Pydantic object.

The `SearchStrategy` contains:

- **`pubmed_queries`** — two to five PubMed query strings constructed using MeSH controlled vocabulary, Boolean operators (AND, OR, NOT), and field tags (e.g., `[MeSH Terms]`, `[Title/Abstract]`, `[Publication Type]`). The agent is instructed to balance sensitivity (recall) and specificity (precision), including at least one MeSH-focused query and one free-text query.
- **`biorxiv_queries`** — two to three plain-text search phrases for bioRxiv, which does not use MeSH.
- **`mesh_terms`** — a list of identified MeSH terms for documentation in the PRISMA methods section.
- **`rationale`** — a brief explanation of the search strategy choices, also included in the methods section of the output report.

The agent's system prompt explains systematic review methodology and instructs it to generate searches appropriate for PRISMA guidelines. The protocol's title, objective, PICO fields, and date range are injected as dynamic context so the agent has everything it needs to construct domain-appropriate queries without any prior knowledge of the topic.

---

### Step 3 — PubMed Tool retrieves articles

For each query in `plan.pubmed_queries`, the pipeline calls the **PubMed Tool** (`PubMedClient`) in two sub-steps.

**Search:** The tool calls `esearch.fcgi?db=pubmed&term=<query>&retmode=json`, which returns a JSON list of PubMed IDs (PMIDs). The `max_per_query` parameter (default: 20) limits how many PMIDs are returned per query. If a date range was specified in the protocol, the tool passes `datetype=pdat&mindate=<start>&maxdate=<end>` parameters to restrict results. Between every NCBI call the client waits 0.35 seconds to respect the rate limit of three requests per second (raised to ten with an NCBI API key).

**Fetch:** The tool calls `efetch.fcgi?db=pubmed&id=<pmids>&retmode=xml`, processing up to 50 PMIDs per batch. The response is a PubMed XML document containing one record per article. The client uses regular expressions on well-defined XML tags to extract: PMID, title, abstract, up to six authors (with "et al." for more), journal abbreviation, publication year, DOI, PMC ID (present only for open-access articles), MeSH terms, and keywords. Each article becomes an `Article` Pydantic object stored in an `all_articles` dictionary keyed by PMID.

All search results (PMID lists) are cached in SQLite under the `search` namespace, and all individual article records are cached under the `article` namespace, each with a 72-hour TTL. On a repeated run, the pipeline reads from cache without making any network calls.

---

### Step 4 — bioRxiv Tool retrieves preprints

For each query in `plan.biorxiv_queries`, the pipeline calls the **bioRxiv Tool** (`BioRxivClient`). This tool queries the bioRxiv REST API, which returns JSON (not XML, not PDF). The search window defaults to the last 180 days, configurable via `--biorxiv-days`. The API returns results in pages of 30, and the client paginates through up to four pages (up to 120 candidate preprints per query).

Because bioRxiv has no controlled vocabulary or Boolean search syntax, the client applies a word-overlap filter: it counts how many non-trivial words (three or more characters) from the query appear in the preprint's title and abstract. Only preprints with at least two matching words are kept. Each matching preprint becomes an `Article` with `source="biorxiv"` and a PMID-equivalent of `"biorxiv_{doi_suffix}"` to avoid collisions with PubMed IDs.

New articles are added to `all_articles` only if their key is not already present, preventing PubMed articles from being overwritten by bioRxiv duplicates of the same work.

---

### Step 5 — Related Article expansion widens coverage

After the primary searches, the pipeline calls the **Related Articles Tool** (part of `PubMedClient`) to discover additional relevant literature through citation similarity. It takes the top eight PubMed PMIDs (non-bioRxiv articles) as seeds and calls `elink.fcgi?dbfrom=pubmed&db=pubmed&LinkName=pubmed_pubmed_related&id=<seeds>`. This returns up to fifteen related PMIDs per depth level.

The pipeline fetches these related articles and marks them with `source="related_1"` (or `related_2` for depth 2, etc.). If `related_depth > 1`, the newly found PMIDs become the next round of seeds and the process repeats. New articles are added to `all_articles` only if not already seen. The default depth of 1 broadens coverage meaningfully without risk of topic drift; depth 2 and above is available but increases noise.

---

### Step 6 — Citation Hops add forward and backward links

In parallel with related article expansion, the pipeline also performs citation hops — following both backward citations (articles that this set cites) and forward citations (articles that have cited this set). The **Citation Hops Tool** calls `elink.fcgi` twice for each hop:

- **Backward (neighbor_score):** `LinkName=pubmed_pubmed_related` — articles similar to the seeds based on shared references and MeSH terms
- **Forward (cited-by):** `LinkName=pubmed_pubmed_citedin` — articles that have cited the seeds

The seeds for citation hopping are the top five PubMed PMIDs, and each hop fetches up to 15 articles per direction. Articles found via citation hopping are marked `source="hop_1"` and carry `hop_level` and `parent_id` fields for traceability. The number of citation hops is controlled by the `--hops` parameter (default: 1, maximum: 4).

---

### Step 7 — Deduplication removes overlapping records

By the time steps 3 through 6 are complete, `all_articles` may contain the same study retrieved from multiple sources — as a PubMed search result, as a related article, and as a citation hop result. The pipeline deduplicates this collection using a simple but effective rule: the key for each article is its DOI in lowercase (trimmed of whitespace), falling back to the PMID if no DOI is available. The first occurrence of each key is kept; subsequent occurrences are discarded.

The number removed is stored in `flow.duplicates_removed`, which becomes part of the PRISMA flow diagram in the output report. After deduplication the pipeline has a clean list called `deduped` — every unique article, once — which enters screening.

---

### Step 8 — Agent 2 screens titles and abstracts (inclusive bias)

The pipeline calls **Agent 2**, the `screening_agent`, on the full deduplicated article list. This is the same agent used for both screening stages, but with different instructions and batch parameters. At the title/abstract stage the agent is instructed to be **INCLUSIVE**: when evidence is ambiguous or incomplete, it should include the article rather than exclude it. This is standard PRISMA methodology — the risk of erroneously excluding a relevant article at the abstract stage is greater than the cost of unnecessarily sending a few extra articles to full-text screening.

Articles are processed in batches of 15. For each batch, the agent receives the title and abstract of each article along with the protocol's inclusion and exclusion criteria injected as dynamic context. It returns a `ScreeningBatchResult` containing one `ScreeningDecision` per article, with fields:

- `decision` — `INCLUDE` or `EXCLUDE`
- `reason` — a short explanation for the decision
- `relevance_score` — a float from 0 to 1 indicating how relevant the article appears to be to the research question

If the agent call fails for any reason (network error, LLM refusal, parsing failure), the pipeline auto-includes the entire batch and logs the error. This ensures that no articles are lost due to transient failures. Included articles go into `ta_included`; excluded articles go into `ta_excluded` with their reasons logged to the screening log.

The PRISMA flow counts are updated at the end of this step: `flow.screened_title_abstract` equals the size of `deduped`, and `flow.excluded_title_abstract` equals the size of `ta_excluded`.

---

### Step 9 — PMC Full-text Fetch populates article bodies

Before full-text eligibility screening can happen, the pipeline needs the actual article text. It filters `ta_included` to find articles that have a `pmc_id` field — indicating they are open-access in PubMed Central — and calls the **PMC Full-text Tool** using `efetch.fcgi?db=pmc&id=<pmc_id>&rettype=xml`. The response is the full article XML.

The client extracts the content of the `<body>` XML tag, strips all remaining HTML and XML tags using regular expressions, normalises whitespace, and truncates to 12,000 characters. These are stored in `article.full_text`. The tool processes up to ten PMC IDs per call with the same 0.35-second rate limiting.

Articles without a PMC ID cannot have their full text retrieved. These articles proceed to full-text screening anyway and the agent is instructed to auto-include them when no full text is available, since their eligibility cannot be properly assessed. Articles where retrieval fails are counted in `flow.not_retrieved`.

---

### Step 10 — Agent 2 screens full text (strict bias)

The pipeline calls **Agent 2** again — the same `screening_agent` — but this time on the articles that passed title/abstract screening, in batches of ten, with **STRICT** eligibility assessment. At this stage the agent has access to the full article text (where available) and is instructed to apply the inclusion and exclusion criteria rigorously. It is no longer in "when in doubt, include" mode; it should now exclude any article that does not clearly satisfy all inclusion criteria or clearly violates an exclusion criterion.

For each excluded article, the agent provides a specific exclusion reason, which the pipeline tallies into `flow.excluded_reasons` — a dictionary mapping reason strings to counts. This appears in the PRISMA flow diagram and methods section of the output report. The top eight most common exclusion reasons are shown.

Articles that pass this stage go into `ft_included` — the final set of articles included in the review. `flow.included_synthesis` equals the length of this list.

The two-stage screening design is the heart of PRISMA methodology: a recall-maximising first filter ensures no relevant article is missed, and a precision-maximising second filter ensures only truly eligible articles enter the synthesis.

---

### Step 11 — Agent 9 extracts evidence spans from included articles

The pipeline calls **Agent 9**, the `evidence_extraction_agent`, on the included articles in batches of five. For each batch, the agent receives the title, abstract, and full text of each article alongside the research question. It identifies and returns two to five `EvidenceSpan` objects per article — the most relevant sentences or passages for answering the research question.

Each span carries:
- `text` — an exact quote or close paraphrase from the article
- `claim` — a short label describing what the span claims (e.g., "BDNF levels reduced in MDD patients vs. controls")
- `section` — where in the article the span came from (abstract, results, discussion, etc.)
- `relevance_score` — a float from 0 to 1 indicating direct relevance to the question
- `is_quantitative` — `True` if the span contains numerical results, p-values, or effect sizes

After extraction, the pipeline deduplicates spans using word-overlap: any two spans with a Jaccard overlap of 0.70 or greater are considered near-duplicates, and the lower-scoring one is removed. The deduplicated spans are sorted by relevance score and capped at **30** as the evidence pool. Of these 30, only the **top 20** are passed to the Synthesis Agent prompt; the full pool of 30 is stored in `PRISMAReviewResult.evidence_spans` and appears in the evidence appendix of the output report.

---

### Step 12 — Agent 4 extracts structured study data (optional)

If the `--extract-data` flag was passed (or `data_items` were specified programmatically), the pipeline calls **Agent 4**, the `data_extraction_agent`, on each included article individually. The agent reads the full article and populates a `StudyDataExtraction` Pydantic object with:

- `study_design` — e.g., "randomised controlled trial", "cohort study"
- `sample_size` — number of participants
- `population` — description of the study population
- `intervention` — what was done to the intervention group
- `outcomes` — the outcomes measured
- `key_findings` — the main results stated in plain language
- `effect_measures` — quantitative effect sizes if reported (e.g., odds ratio, mean difference)

This structured extraction is optional because it adds one LLM call per article, which can be costly for large reviews. When performed, the data appears in the study characteristics table of the output Markdown report.

---

### Step 13 — Agent 3 assesses risk of bias for each included article

The pipeline calls **Agent 3**, the `rob_agent`, on each included article individually. The agent is told which risk-of-bias tool the protocol specifies (from the eleven supported options) and receives the corresponding domain list from the `ROB_DOMAINS` dictionary in `agents.py`. For RoB 2, these domains are: randomisation process, deviations from intended interventions, missing outcome data, measurement of the outcome, and selection of the reported result.

For each domain, the agent makes a judgment of LOW, SOME CONCERNS, HIGH, or UNCLEAR risk, and provides a brief justification. It then makes an overall judgment. All of this is stored as a `RiskOfBiasResult` on `article.risk_of_bias`. The overall judgments appear in the study characteristics table and the risk-of-bias summary section of the report.

The dynamic context injected from the protocol is minimal but critical: the `rob_tool` field determines which domain list the agent uses, making the same agent capable of conducting any of the eleven assessment types without code changes.

---

### Step 14 — Four agents run in parallel to produce the synthesis

Once all article-level analysis is complete, the pipeline uses `asyncio.gather()` to run four independent agents simultaneously. These agents do not depend on each other's outputs, so there is no reason to run them sequentially.

**Agent 5: synthesis_agent** receives the **first 25 included articles in collection order** (articles are not ranked by relevance before this slice — they appear in the order they passed full-text screening), the **top 20 evidence spans by relevance score** (from a pool of up to 30 after deduplication), and a textual summary of the PRISMA flow counts. It produces a Markdown narrative synthesis with thematic organisation — grouping findings by theme rather than by study — citing each claim in the format `(Author et al., Year; PMID: XXXXX)`. Where studies contradict each other, the agent is instructed to note the contradiction explicitly rather than glossing over it. This narrative becomes the core of the Results section in the output report.

**Agent 7: bias_summary_agent** receives the list of included studies with their risk-of-bias assessments and produces a plain-text overall quality assessment. It comments on the proportion of studies at high versus low risk, identifies the most common methodological weaknesses across the included literature, discusses concerns about publication bias and heterogeneity, and states the overall confidence in the body of evidence.

**Agent 6: grade_agent** is called once per outcome of interest. GRADE (Grading of Recommendations, Assessment, Development and Evaluations) is a framework for rating the certainty of a body of evidence across five domains: risk of bias, inconsistency across studies, indirectness of the evidence, imprecision of results, and publication bias. For each outcome, the agent produces a `GRADEAssessment` with per-domain ratings and an overall certainty rating of HIGH, MODERATE, LOW, or VERY LOW. The pipeline calls this agent for up to three outcomes by default, running all three in parallel within the `asyncio.gather()` call.

**Agent 8: limitations_agent** receives the PRISMA flow summary (which tells it how many articles were excluded and why) and the list of included studies. It produces a two-to-three paragraph limitations section covering: the scope of the database search and any databases not searched, the impact of language or date restrictions, the risk of selection bias in the screening process, the reliance on AI-assisted screening (which the agent is instructed to mention as a limitation with a caveat), and the degree of heterogeneity that prevents firm meta-analytic conclusions.

The results of all four parallel tasks are collected by `asyncio.gather()`. If any individual task fails, the exception is caught and the corresponding output is stored as an empty string or `None`, while the other tasks are unaffected.

---

### Step 15 — Assemble the final result

With all analysis complete, the pipeline constructs a `PRISMAReviewResult` Pydantic object that holds everything:

- `protocol` — the original `ReviewProtocol`
- `flow` — the `PRISMAFlowCounts` with all counts accumulated during the run
- `included_articles` — the final list of `Article` objects, each carrying their full-text, extracted data, and risk-of-bias assessment
- `screening_log` — every screening decision made, with reasons, for auditability
- `evidence_spans` — the deduplicated, relevance-sorted evidence spans
- `synthesis_text` — the Markdown narrative from Agent 5
- `bias_assessment` — the overall quality text from Agent 7
- `grade_assessments` — a dictionary mapping outcome names to `GRADEAssessment` objects
- `limitations` — the limitations text from Agent 8
- `timestamp` — when the review was completed

This object is passed to the export layer.

---

### Step 16 — Export to PRISMA 2020 report

The `export.py` module renders the `PRISMAReviewResult` into the requested output formats, saved to `prisma_results/{slug}_{timestamp}.{ext}`:

**Markdown** produces a complete PRISMA 2020 structured report with eight sections: Abstract (with structured summary), Introduction (rationale and PICO), Methods (search strategy, eligibility criteria, databases, RoB tool, selection process), Results (PRISMA flow diagram as a table, study characteristics table, narrative synthesis, risk-of-bias summary, GRADE certainty table), Discussion (limitations section from Agent 8), Other Information (registration, conflicts of interest placeholders), References (numbered with DOI links), and an Appendix with the top 20 evidence spans. This document is ready to submit to a journal or supervisor with minimal additional editing.

**JSON** serialises the entire `PRISMAReviewResult` via Pydantic's `model_dump_json()`, including all nested objects and lists. This format is suitable for programmatic post-processing, ingestion into a database, or building a web interface on top of the review data.

**BibTeX** produces `@article` entries for each included study, with title, authors, journal, year, DOI, and PMID fields. This file can be imported directly into Zotero, Mendeley, or a LaTeX reference manager.

---

## 4. The Pipeline Orchestrator

The `PRISMAReviewPipeline` class in `pipeline.py` is the central coordinator. It holds references to both database clients and is responsible for:

- Calling each step in the correct order
- Maintaining the `all_articles` dictionary and the `seen` set to prevent duplicate processing
- Tracking all PRISMA flow counts as articles move through each stage
- Keeping a `screening_log` of every inclusion and exclusion decision for auditability
- Passing progress messages to an optional callback so the CLI can print step names
- Using `asyncio.gather()` for the parallel synthesis step (Step 14)
- Catching exceptions at every step so a failure in one article's risk-of-bias assessment does not abort the entire pipeline
- Assembling the final `PRISMAReviewResult` from all the pieces

The constructor accepts all configuration — API keys, model name, protocol, cache path, search parameters — so that the same class works from the CLI, from a FastAPI endpoint, or from a Jupyter notebook.

---

## 5. The 9 LLM Agents

All nine agents share the same structural pattern. Each is a pydantic-ai `Agent` with a static system prompt containing generic methodological instructions, plus a dynamic context decorator (`@agent.system_prompt`) that injects the specific `ReviewProtocol` fields at call time. All agents use `defer_model_check=True` so the OpenRouter model is injected at runtime via `build_model(api_key, model_name)`. This means any of the 100+ OpenRouter models can be used without touching agent code.

| Agent | Role | Batch Size | Output Type | Bias / Policy |
|---|---|---|---|---|
| 1. search_strategy_agent | Generate database queries | Single call | SearchStrategy | — |
| 2. screening_agent (T/A) | Title/abstract eligibility | 15 articles | ScreeningBatchResult | INCLUSIVE |
| 2. screening_agent (FT) | Full-text eligibility | 10 articles | ScreeningBatchResult | STRICT |
| 3. rob_agent | Risk of bias per study | Per article | RiskOfBiasResult | Tool from protocol |
| 4. data_extraction_agent | Structured data per study | Per article | StudyDataExtraction | Optional |
| 5. synthesis_agent | Narrative synthesis | first 25 articles (collection order) + top 20 spans | str (Markdown) | Thematic, cited |
| 6. grade_agent | GRADE certainty per outcome | Per outcome | GRADEAssessment | 5-domain framework |
| 7. bias_summary_agent | Overall quality summary | All included articles | str | Cross-study view |
| 8. limitations_agent | Review limitations | Flow text + articles | str | PRISMA caveat |
| 9. evidence_extraction_agent | Key evidence spans | 5 articles | BatchEvidenceExtraction | Relevance-ranked |

The two-stage screening design deserves special attention. Agents 2a and 2b are the same agent but receive different instructions through their system prompts depending on which stage is active. At title/abstract stage, the prompt says: *"when in doubt, include — it is better to over-include and screen full text than to miss a relevant study."* At full-text stage, the prompt says: *"apply the eligibility criteria strictly — only include studies that clearly satisfy all inclusion criteria."* This mirrors PRISMA 2020 best practice and is the primary reason screening has two stages rather than one.

---

## 6. Data Collection Tools

The two HTTP clients in `clients.py` act as data collection tools for the pipeline. They are plain Python classes — not pydantic-ai `@agent.tool` functions — called directly by the pipeline orchestrator. All results are cached in SQLite before being returned.

### PubMedClient

The PubMed client wraps the NCBI E-utilities API. It makes three types of calls:

**Search** (`esearch.fcgi`): Takes a query string and optional date parameters, returns a JSON list of PMIDs. The client sends `retmode=json`, `usehistory=n`, and the NCBI tool and email parameters. Results are cached under the `search` namespace.

**Fetch** (`efetch.fcgi`): Takes a list of PMIDs, sends them in batches of 50 with `retmode=xml`, and returns a PubMed XML document. The client uses regular expressions to extract all article metadata fields. Individual article records are cached under the `article` namespace so that the same PMID is never fetched twice within the 72-hour window.

**elink** (`elink.fcgi`): Used for both related article discovery and citation hopping. The `LinkName=pubmed_pubmed_related` link returns articles similar to the seeds based on NCBI's citation graph. The `LinkName=pubmed_pubmed_citedin` link returns articles that have cited the seeds. Results from the related search are cached under `related`.

**Full-text** (`efetch.fcgi?db=pmc`): Takes PMC IDs, fetches full XML, extracts the `<body>` tag, strips all tags, normalises whitespace, truncates to 12,000 characters. Cached under `fulltext`.

Between every NCBI call, the client waits 0.35 seconds to respect the 3 req/s rate limit without an API key (10 req/s with key). The tool and email parameters identify the application to NCBI, which is required for production use.

### BioRxivClient

The bioRxiv client queries the bioRxiv REST API. Because bioRxiv has no MeSH vocabulary or Boolean syntax, the client uses a simple word-overlap heuristic: it counts words of three or more characters that appear in both the query and the preprint's title plus abstract, and keeps only preprints where at least two query words match. The client paginates through results in steps of 30, stopping after 120 candidates. Results are cached under `biorxiv`.

---

## 7. SQLite Cache

Every network request is wrapped in a cache check. Before any API call, the client computes a SHA256 hash of `namespace:identifier` and looks it up in the SQLite `cache.db` file. If a record exists and was created less than 72 hours ago, it is returned without any network call. If not, the request is made, the response is stored as JSON, and the result is returned.

The five namespaces are: `search` (PubMed PMID lists), `article` (individual article records), `related` (elink PMID lists), `fulltext` (PMC body text), and `biorxiv` (bioRxiv search results). The 72-hour TTL means that a researcher can re-run a review with different screening criteria or a different LLM model and all the database fetches will come from cache, making the re-run much faster and placing no additional load on NCBI or bioRxiv.

Expiry is checked on read: when a cached record is retrieved, the client checks whether `(now - created_at) > ttl_hours`. Expired records are deleted on the read path rather than by a background process, which avoids the need for any scheduler.

---

## 8. PRISMA Flow Counting

PRISMA 2020 requires a flow diagram that documents exactly how many articles were identified, screened, and included at each stage. The `PRISMAFlowCounts` model tracks all of these numbers throughout the pipeline run:

- **`db_pubmed`** — articles retrieved from PubMed searches (Step 3)
- **`db_biorxiv`** — articles retrieved from bioRxiv (Step 4)
- **`db_related`** — articles added through related-article expansion (Step 5)
- **`db_hops`** — articles added through citation hopping (Step 6)
- **`total_identified`** — sum of the above (before deduplication)
- **`duplicates_removed`** — articles removed by deduplication
- **`after_dedup`** — articles entering screening
- **`screened_title_abstract`** — same as `after_dedup` (all go through T/A screening)
- **`excluded_title_abstract`** — articles excluded at T/A stage
- **`sought_fulltext`** — articles that passed T/A screening
- **`not_retrieved`** — articles where full text could not be obtained
- **`assessed_eligibility`** — same as `sought_fulltext`
- **`excluded_eligibility`** — articles excluded at full-text stage, with reasons
- **`excluded_reasons`** — dictionary of exclusion reason strings to counts
- **`included_synthesis`** — articles included in the final synthesis

These counts are updated at each relevant pipeline step and are rendered into a PRISMA flow table in the Markdown output.

---

## 9. Graceful Degradation

The pipeline is designed to always return a result, even if individual steps fail. Key degradation behaviours:

- **Screening batch failure:** If the LLM call for a batch of articles fails, the entire batch is auto-included and the error is logged. This ensures no articles are lost due to transient failures — it is better to include extras than to miss potentially relevant studies.
- **Full-text screening without full text:** If an article has no PMC ID (and therefore no full text), it is auto-included at the full-text stage because its eligibility cannot be properly assessed without the text.
- **Evidence/data/RoB extraction failure:** If the agent fails for a specific article, that article is skipped and the pipeline continues with the remaining articles. The failure is logged.
- **Parallel synthesis task failure:** If one of the four parallel tasks in Step 14 raises an exception, `asyncio.gather(return_exceptions=True)` catches it and stores the exception object. The corresponding output field is set to an empty string or `None`, but the other three tasks complete normally.

This means a complete pipeline failure requires every single step and every single fallback to fail simultaneously — which is extremely unlikely in practice.

---

## 10. Technology Stack

| Component | Technology | Reason |
|---|---|---|
| Agent framework | pydantic-ai ≥ 1.0 | Typed structured outputs; automatic retry on validation failure; model-agnostic |
| Data validation | Pydantic v2 | All models runtime-validated; automatic JSON serialisation; strict mode |
| LLM access | OpenRouter API | 100+ models; no vendor lock-in; cost/capability tunable per step |
| HTTP client | httpx ≥ 0.25 | Async-capable; used synchronously within rate-limited clients |
| Cache | SQLite (built-in) | Zero external dependencies; persists across runs; SHA256-keyed |
| Async | asyncio | `asyncio.gather()` for Step 14 parallel synthesis |
| Python version | 3.11+ | Required for pydantic-ai type features |
| Build system | Hatchling | Modern Python packaging; PyPI distribution |
| CLI | argparse | Standard library; no extra dependencies |

---

## 11. Known Limitations

**Only PubMed and bioRxiv:** PRISMA reviews should search multiple databases (Embase, CINAHL, PsycINFO, Cochrane, etc.). The current implementation only supports NCBI PubMed and bioRxiv REST APIs. Adding further databases would require implementing additional client classes.

**Regex XML parsing:** PubMed and PMC full-text responses are parsed with regular expressions on XML tags. This is fragile to NCBI schema changes. A proper parser using `xml.etree.ElementTree` would be more robust.

**Full-text retrieval limited to open-access PMC:** Articles behind paywalls cannot have their full text retrieved. These articles are auto-included at the full-text screening stage, which may result in including ineligible studies that would have been excluded if the full text were available.

**LLM-based screening is not reproducible:** Different LLM providers and model versions may make different screening decisions for the same article. The output report includes the AI-screening caveat in the limitations section, but this is a fundamental limitation of the approach.

**No meta-analysis:** The system produces narrative synthesis, risk-of-bias assessment, and GRADE certainty, but does not perform statistical meta-analysis (forest plots, heterogeneity statistics). A separate tool would be needed for quantitative synthesis.

**Single-reviewer screening:** PRISMA best practice calls for dual independent screening with adjudication of disagreements. The current system uses a single LLM call per batch. Implementing dual-reviewer simulation (two independent calls with disagreement detection) would increase reliability.

**Serial article processing:** Risk-of-bias assessment and data extraction are performed per-article sequentially. Parallelising these with `asyncio.gather()` would significantly reduce runtime for large reviews.

---

*Generated: 2026-04-12 | PRISMA Review Agent v1.0.0*
