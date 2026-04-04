# Developer Documentation — PRISMA Review Agent

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Repository Layout](#repository-layout)
- [Module Responsibilities](#module-responsibilities)
- [Data Models](#data-models)
- [Pipeline Flow — Step by Step](#pipeline-flow--step-by-step)
- [Agent Architecture](#agent-architecture)
- [HTTP Clients & Caching](#http-clients--caching)
- [Data Storage & State Management](#data-storage--state-management)
- [PRISMA Flow Diagram — How It Works](#prisma-flow-diagram--how-it-works)
- [Design Decisions](#design-decisions)
- [Adding a New Agent](#adding-a-new-agent)
- [Environment & Configuration](#environment--configuration)

---

## Architecture Overview

The system is a **fully async Python pipeline** that automates PRISMA 2020 systematic literature reviews. It chains HTTP-based data acquisition (PubMed, bioRxiv) with LLM-powered analysis (screening, synthesis, risk of bias, etc.) using [pydantic-ai](https://docs.pydantic.dev/latest/integrations/pydantic_ai/) agents that return strongly-typed, validated outputs.

```mermaid
graph TD
    A[CLI: main.py] --> P
    B[Library: pipeline.py] --> P
    P[PRISMAReviewPipeline\n15-step async orchestrator]
    P --> C[HTTP Clients\nclients.py]
    P --> D[pydantic-ai Agents\nagents.py · 9 typed agents]
    C --> M[Pydantic v2 Models\nmodels.py]
    D --> M
    M --> E[Export\nexport.py]
    E --> F1[Markdown]
    E --> F2[JSON]
    E --> F3[BibTeX]

    C --- C1[PubMedClient]
    C --- C2[BioRxivClient]
    C --- C3[Cache · SQLite]
    D --- D1[OpenRouter API]
```

---

## Repository Layout

```
prisma-review-agent/
├── main.py               # CLI entry point (argparse)
├── pipeline.py           # Core async orchestrator (PRISMAReviewPipeline)
├── agents.py             # 9 pydantic-ai agents + runner functions
├── models.py             # All Pydantic v2 data models
├── clients.py            # HTTP clients: PubMedClient, BioRxivClient, Cache
├── evidence.py           # Evidence extraction entry point (wraps agent)
├── export.py             # to_markdown(), to_json(), to_bibtex()
├── __init__.py           # Root package (dev use)
├── prisma_review_agent/  # Installable package (mirrors root)
│   ├── __init__.py       # Public API re-exports
│   └── *.py              # (same modules as above)
├── pyproject.toml        # Build config, deps, entry point
└── developer.md          # This file
```

The `prisma_review_agent/` package is the installable form; the root-level `.py` files are for direct development use. Both contain the same code.

---

## Module Responsibilities

| Module | Responsibility | Key Types |
|---|---|---|
| `models.py` | All Pydantic v2 data models — no logic | `Article`, `ReviewProtocol`, `PRISMAFlowCounts`, `PRISMAReviewResult`, etc. |
| `clients.py` | HTTP data acquisition + SQLite cache | `PubMedClient`, `BioRxivClient`, `Cache` |
| `agents.py` | LLM agent definitions + async runners | `AgentDeps`, 9 `Agent` instances, `run_*` functions |
| `pipeline.py` | Async orchestrator — calls clients & agents in order | `PRISMAReviewPipeline.run()` |
| `evidence.py` | Thin wrapper — delegates to `run_evidence_extraction` | `extract_evidence()` |
| `export.py` | Output formatters | `to_markdown()`, `to_json()`, `to_bibtex()` |
| `main.py` | CLI argument parsing + `ReviewProtocol` construction | `main()`, `run_review()` |

---

## Data Models

### Core Model Relationships

```mermaid
classDiagram
    class ReviewProtocol {
        +str title
        +str objective
        +str pico_population
        +str pico_intervention
        +str pico_comparison
        +str pico_outcome
        +str inclusion_criteria
        +str exclusion_criteria
        +list~str~ databases
        +str date_range_start
        +str date_range_end
        +int max_hops
        +RoBTool rob_tool
        +pico_text() str
        +question() str
    }

    class Article {
        +str pmid
        +str doi
        +str pmc_id
        +str title
        +str abstract
        +str authors
        +str journal
        +str year
        +list~str~ mesh_terms
        +list~str~ keywords
        +str source
        +str full_text
        +int hop_level
        +InclusionStatus inclusion_status
        +str exclusion_reason
        +float quality_score
        +RiskOfBiasResult risk_of_bias
        +StudyDataExtraction extracted_data
        +citation() str
        +to_context_block() str
    }

    class EvidenceSpan {
        +str text
        +str claim
        +str section
        +str paper_pmid
        +str paper_title
        +str doi
        +float relevance_score
    }

    class PRISMAFlowCounts {
        +int db_pubmed
        +int db_biorxiv
        +int db_related
        +int db_hops
        +int total_identified
        +int duplicates_removed
        +int after_dedup
        +int screened_title_abstract
        +int excluded_title_abstract
        +int sought_fulltext
        +int not_retrieved
        +int assessed_eligibility
        +int excluded_eligibility
        +dict excluded_reasons
        +int included_synthesis
    }

    class PRISMAReviewResult {
        +str research_question
        +list~str~ search_queries
        +str synthesis_text
        +str bias_assessment
        +str limitations
        +str timestamp
    }

    class RiskOfBiasResult {
        +list~RoBDomainAssessment~ assessments
        +RoBJudgment overall
        +str summary
    }

    class StudyDataExtraction {
        +str study_design
        +str sample_size
        +str population
        +str intervention
        +list~str~ outcomes
        +list~str~ key_findings
        +list~str~ effect_measures
        +str follow_up
        +str funding
    }

    class GRADEAssessment {
        +str outcome
        +dict domains
        +GRADECertainty overall_certainty
        +str summary
    }

    PRISMAReviewResult "1" --> "1" ReviewProtocol
    PRISMAReviewResult "1" --> "1" PRISMAFlowCounts
    PRISMAReviewResult "1" --> "*" Article
    PRISMAReviewResult "1" --> "*" EvidenceSpan
    PRISMAReviewResult "1" --> "*" GRADEAssessment
    Article "1" --> "0..1" RiskOfBiasResult
    Article "1" --> "0..1" StudyDataExtraction
```

### LLM Output Models (agent → Pydantic)

| Agent | Output Model | Key Fields |
|---|---|---|
| `search_strategy_agent` | `SearchStrategy` | `pubmed_queries[]`, `biorxiv_queries[]`, `mesh_terms[]`, `rationale` |
| `screening_agent` | `ScreeningBatchResult` | `decisions[ScreeningDecision]` → `index, decision, reason, relevance_score` |
| `rob_agent` | `RiskOfBiasResult` | `assessments[RoBDomainAssessment]`, `overall: RoBJudgment`, `summary` |
| `data_extraction_agent` | `StudyDataExtraction` | `study_design`, `sample_size`, `outcomes[]`, `key_findings[]`, `effect_measures[]` |
| `synthesis_agent` | `str` | Full narrative synthesis in Markdown |
| `grade_agent` | `GRADEAssessment` | `domains{}, overall_certainty: GRADECertainty`, `summary` |
| `bias_summary_agent` | `str` | Overall bias narrative |
| `limitations_agent` | `str` | Limitations section (2–3 paragraphs) |
| `evidence_extraction_agent` | `BatchEvidenceExtraction` | `articles[ArticleEvidenceExtraction]` → `evidence[ExtractedEvidenceItem]` |

---

## Pipeline Flow — Step by Step

`PRISMAReviewPipeline.run()` in [pipeline.py](pipeline.py) executes 15 ordered steps. Steps 1–13 are sequential; step 14 runs three tasks in parallel via `asyncio.gather()`.

```mermaid
flowchart TD
    INPUT([ReviewProtocol\nPICO · criteria · databases · max_hops])

    S1["Step 1 — Search Strategy · LLM\nrun_search_strategy(deps)\n→ SearchStrategy\npubmed_queries[] · biorxiv_queries[]"]

    S2["Step 2 — PubMed Search\nesearch + efetch\nper query → Article[]"]
    S3["Step 3 — bioRxiv Search\nREST API · keyword match ≥2 words\n→ Article[]"]
    S4["Step 4 — Related Articles\nelink neighbor_score\nseeds = pm_pmids[:8]"]
    S5["Step 5 — Citation Hops\nfind_related() + find_cited_by()\nup to max_hops iterations\na.hop_level · a.parent_id set"]

    S6["Step 6 — Deduplication\nkey = doi.lower() if doi else pmid\nflow.duplicates_removed updated"]

    S7["Step 7 — Title/Abstract Screening · LLM\nrun_screening(batch, deps, 'title_abstract')\nbatch size = 15 · INCLUSIVE bias\nfailure → auto-include batch\n→ ScreeningBatchResult"]

    S8["Step 8 — Full-text Retrieval\nfetch_full_text(pmc_ids)\narticle.full_text populated\nup to 12 000 chars"]

    S9["Step 9 — Full-text Eligibility · LLM\nrun_screening(batch, deps, 'full_text')\nbatch size = 10 · STRICT\nno full_text → auto-include\nexcluded_reasons tallied"]

    S10["Step 10 — Evidence Extraction · LLM\nextract_evidence(ft_included, deps)\nbatch size = 5 · 2–5 spans/article\ndedup by word overlap ≥ 0.7\nsorted by relevance_score desc · max 30"]

    S11["Step 11 — Data Extraction · LLM  optional\nrun_data_extraction(article, data_items, deps)\narticle.extracted_data = StudyDataExtraction\nonly runs if data_items passed to run()"]

    S12["Step 12 — Risk of Bias · LLM\nrun_risk_of_bias(article, deps) per article\narticle.risk_of_bias = RiskOfBiasResult\ndomains from ROB_DOMAINS dict"]

    S13["Step 13 — Narrative Synthesis · LLM\nrun_synthesis(ft_included, evidence, flow_text, deps)\n→ str Markdown with PMID citations\nmax 25 articles + top 20 evidence spans"]

    PAR["Step 14 — asyncio.gather PARALLEL"]
    S14A["run_bias_summary()\n→ str"]
    S14B["run_limitations()\n→ str"]
    S14C["run_grade(outcome) × 3\n→ GRADEAssessment each"]

    S15["Step 15 — Assemble PRISMAReviewResult\nprotocol · flow · included_articles\nscreening_log · evidence_spans\nsynthesis · bias · limitations · grade"]

    EXPORT["Export caller-side\nto_markdown() · to_json() · to_bibtex()"]

    INPUT --> S1
    S1 --> S2
    S1 --> S3
    S1 --> S4
    S2 --> S5
    S3 --> S5
    S4 --> S5
    S5 --> S6
    S6 --> S7
    S7 -->|ta_included| S8
    S8 --> S9
    S9 -->|ft_included| S10
    S10 --> S11
    S11 --> S12
    S12 --> S13
    S13 --> PAR
    PAR --> S14A
    PAR --> S14B
    PAR --> S14C
    S14A --> S15
    S14B --> S15
    S14C --> S15
    S15 --> EXPORT
```

### Batch Sizes

| Step | Batch Size | Reason |
|---|---|---|
| Title/Abstract screening | 15 articles | Balance token cost vs. context length |
| Full-text screening | 10 articles | Larger input per article (up to 12k chars) |
| Evidence extraction | 5 articles | Highest per-article token cost; accuracy matters |
| PubMed efetch | 50 PMIDs | NCBI recommended limit |
| Full-text PMC fetch | 10 articles | Rate limit + response size |

---

## Agent Architecture

All agents follow the same pattern: declared once as a module-level constant, model injected at call time via `build_model()`.

```mermaid
flowchart LR
    subgraph Declaration["Module-level · agents.py"]
        A["Agent(\n  output_type=SomePydanticModel,\n  deps_type=AgentDeps,\n  system_prompt='...',\n  retries=2,\n  defer_model_check=True\n)"]
        CTX["@agent.system_prompt\nasync def _context(ctx)\n  → inject protocol fields"]
    end

    subgraph Runner["Runner function · async"]
        R1["model = build_model(\n  deps.api_key,\n  deps.model_name\n)"]
        R2["result = await agent.run(\n  user_prompt,\n  deps=deps,\n  model=model\n)"]
        R3["return result.output\n  → validated Pydantic model"]
    end

    subgraph Deps["AgentDeps · dataclass"]
        D1["protocol: ReviewProtocol"]
        D2["api_key: str"]
        D3["model_name: str"]
    end

    subgraph Provider["LLM Backend"]
        P1["OpenRouterProvider\n+ OpenAIChatModel"]
        P2["Any model on OpenRouter\nClaude · GPT-4o · Gemini\nDeepSeek · Llama · ..."]
    end

    Deps --> Runner
    Declaration --> Runner
    Runner --> Provider
```

### Agent Map

| # | Agent | Runner | Output |
|---|---|---|---|
| 1 | `search_strategy_agent` | `run_search_strategy(deps)` | `SearchStrategy` |
| 2 | `screening_agent` | `run_screening(articles, deps, stage)` | `ScreeningBatchResult` |
| 3 | `rob_agent` | `run_risk_of_bias(article, deps)` | `RiskOfBiasResult` |
| 4 | `data_extraction_agent` | `run_data_extraction(article, items, deps)` | `StudyDataExtraction` |
| 5 | `synthesis_agent` | `run_synthesis(articles, evidence, flow, deps)` | `str` |
| 6 | `grade_agent` | `run_grade(outcome, articles, deps)` | `GRADEAssessment` |
| 7 | `bias_summary_agent` | `run_bias_summary(articles, deps)` | `str` |
| 8 | `limitations_agent` | `run_limitations(flow, articles, deps)` | `str` |
| 9 | `evidence_extraction_agent` | `run_evidence_extraction(articles, deps)` | `BatchEvidenceExtraction` |

---

## HTTP Clients & Caching

### PubMedClient — NCBI E-utilities call chain

```mermaid
flowchart TD
    subgraph search["search(query, max, date_start, date_end)"]
        S1["cache.get('search', key)"] -->|HIT| SR[return pmids]
        S1 -->|MISS| S2["GET esearch.fcgi\n?db=pubmed&term=...&retmax=..."]
        S2 --> S3["cache.set('search', key, pmids)\nreturn pmids"]
    end

    subgraph fetch["fetch_articles(pmids)"]
        F1["for each pmid:\ncache.get('article', pmid)"] -->|HIT| FA[Article from cache]
        F1 -->|MISS| FB[add to uncached list]
        FB --> F2["GET efetch.fcgi\n?db=pubmed&id=batch50&rettype=xml"]
        F2 --> F3["_parse_xml(xml) → Article[]\nregex: pmid, title, abstract,\nauthors, journal, year,\ndoi, pmc_id, mesh_terms, keywords"]
        F3 --> F4["cache.set('article', pmid, Article.model_dump())"]
    end

    subgraph related["find_related(pmids)"]
        R1["cache.get('related', sorted_pmids)"] -->|HIT| RR[return pmids]
        R1 -->|MISS| R2["GET elink.fcgi\n?cmd=neighbor_score\n&linkname=pubmed_pubmed"]
        R2 --> R3["cache.set('related', ...)\nreturn related pmids"]
    end

    subgraph citedin["find_cited_by(pmids)"]
        C1["GET elink.fcgi\n?linkname=pubmed_pubmed_citedin"]
        C1 -->|exception| CE[return empty list]
        C1 --> C2[return cited pmids]
    end

    subgraph fulltext["fetch_full_text(pmc_ids · max 10)"]
        FT1["cache.get('fulltext', pmc_id)"] -->|HIT| FTR[results dict]
        FT1 -->|MISS| FT2["GET efetch.fcgi\n?db=pmc&id=PMCxxxxxx"]
        FT2 --> FT3["extract body XML\nstrip tags · truncate 12 000 chars"]
        FT3 --> FT4["cache.set('fulltext', pmc_id, text)"]
    end
```

Rate limit: `time.sleep(0.35)` before every NCBI request. Providing `NCBI_API_KEY` enables 10 req/s.

### BioRxivClient

```mermaid
flowchart TD
    B1["cache.get('biorxiv', query_days)"] -->|HIT| BR["return Article list"]
    B1 -->|MISS| B2["compute date range\nstart = today − days_back"]
    B2 --> B3["for cursor in range 0,30,60...120\nGET api.biorxiv.org/details/biorxiv\n  /start/end/cursor/30"]
    B3 --> B4{"score = words matching\nquery in title+abstract\nscore >= 2?"}
    B4 -->|yes| B5["Article(\n  pmid='biorxiv_{doi_suffix}',\n  source='biorxiv'\n)"]
    B4 -->|no| B3
    B5 --> B6{"len >= max_results?"}
    B6 -->|yes| B7["cache.set('biorxiv', ...)\nreturn articles[:max_results]"]
    B6 -->|no| B3
```

---

## Data Storage & State Management

The system has three distinct storage layers. There is no database server, no ORM, and no persistent application state beyond the cache file.

```mermaid
graph TD
    subgraph L1["Layer 1 · In-Memory  lives only during pipeline.run()"]
        IM1["all_articles: dict[pmid → Article]"]
        IM2["deduped: list[Article]"]
        IM3["ta_included / ft_included: list[Article]"]
        IM4["all_screening: list[ScreeningLogEntry]"]
        IM5["evidence: list[EvidenceSpan]"]
        IM6["PRISMAReviewResult"]
    end

    subgraph L2["Layer 2 · SQLite Cache  prisma_agent_cache.db  persists 72h"]
        DB1["ns=search   → pmid lists"]
        DB2["ns=article  → Article dicts"]
        DB3["ns=related  → related pmid lists"]
        DB4["ns=fulltext → PMC body text"]
        DB5["ns=biorxiv  → Article dicts"]
    end

    subgraph L3["Layer 3 · Exported Files  prisma_results/"]
        EX1["{slug}.md\nPRISMA 2020 report"]
        EX2["{slug}.json\nfull result dump"]
        EX3["{slug}.bib\nBibTeX references"]
    end

    EXT["External APIs\nNCBI E-utilities · bioRxiv REST"]

    EXT -->|HTTP response| L2
    L2 -->|deserialized Article objects| L1
    L1 -->|PRISMAReviewResult returned| L3
```

---

### Layer 1 — In-Memory Pipeline State

#### How `all_articles` dict is built

```mermaid
flowchart TD
    INIT["all_articles = {}\nseen_pmids = set()"]

    INIT --> ST2["Step 2 · PubMed search per query\npmids = pubmed.search(query)\nnew = pmids not in seen_pmids\narts = pubmed.fetch_articles(new)\nfor a: a.source='pubmed_search'\nall_articles[a.pmid] = a"]

    ST2 --> ST3["Step 3 · bioRxiv search per query\nbx_arts = biorxiv.search(query)\nfor a: if a.pmid not in all_articles:\n  all_articles[a.pmid] = a\n  a.source='biorxiv'"]

    ST3 --> ST4["Step 4 · Related articles\nseeds = pm_pmids[:8]\nfor depth 1..related_depth:\n  rel = pubmed.find_related(seeds)\n  new_rel = rel not in all_articles\n  arts = pubmed.fetch_articles(new_rel)\n  a.source = 'related_{d}'\n  all_articles[a.pmid] = a"]

    ST4 --> ST5["Step 5 · Citation hops\nhop_seeds = pm_pmids[:5]\nfor hop in 1..max_hops:\n  back = find_related(hop_seeds)\n  fwd = find_cited_by(hop_seeds)\n  combined = set(back+fwd)\n  hop_arts = fetch_articles(new[:15])\n  a.source='hop_{hop}'\n  a.hop_level=hop\n  a.parent_id = seeds[:3]\n  all_articles[a.pmid] = a"]

    ST5 --> ST6["Step 6 · Deduplication\nfor a in all_articles.values():\n  key = a.doi.lower() if a.doi else a.pmid\n  if key not in unique_map:\n    unique_map[key] = a\ndeduped = list(unique_map.values())"]
```

#### Article state transitions

```mermaid
stateDiagram-v2
    [*] --> Created : fetch_articles() / biorxiv.search()
    note right of Created
        pmid, title, abstract, authors
        journal, year, doi, pmc_id
        mesh_terms, keywords, source
        full_text = ""
        inclusion_status = PENDING
        risk_of_bias = None
        extracted_data = None
        quality_score = 0.0
    end note

    Created --> Screened_TA : Step 7 · title/abstract screening
    note right of Screened_TA
        quality_score = dec.relevance_score
        inclusion_status = included | excluded
        exclusion_reason = dec.reason (if excluded)
    end note

    Screened_TA --> Excluded_TA : LLM decision = exclude
    Screened_TA --> FullTextFetched : Step 8 · pmc_id present
    Screened_TA --> EligibilitySkipped : no pmc_id → auto-forward

    FullTextFetched --> FullTextFetched : full_text populated\nup to 12 000 chars

    FullTextFetched --> Screened_FT : Step 9 · full-text eligibility
    EligibilitySkipped --> Included : auto-included

    Screened_FT --> Excluded_FT : LLM decision = exclude\nexclusion_reason updated
    Screened_FT --> Included : LLM decision = include

    Included --> DataExtracted : Step 11 · if data_items passed\nextracted_data = StudyDataExtraction
    DataExtracted --> RoBAssessed : Step 12\nrisk_of_bias = RiskOfBiasResult
    Included --> RoBAssessed : Step 12 (skips 11 if no data_items)

    RoBAssessed --> [*] : assembled into\nPRISMAReviewResult.included_articles[]
```

#### EvidenceSpan — extraction and deduplication

```mermaid
flowchart TD
    IN["ft_included: list[Article]"]
    IN --> B1["for batch in articles step 5\n  run_evidence_extraction(batch, deps)"]
    B1 --> B2["LLM → BatchEvidenceExtraction\n  per article: 2–5 ExtractedEvidenceItem\n  quote · claim · section · relevance · is_quantitative"]
    B2 --> B3["flatten to EvidenceSpan[]\n  text=quote · paper_pmid · paper_title\n  section · relevance_score · claim · doi"]
    B3 --> B4["sort by relevance_score descending"]
    B4 --> B5["_deduplicate_spans(spans, threshold=0.7)\n  for each span:\n    words = set(text.lower().split())\n    for existing in kept:\n      overlap = |words ∩ ex_words| / min(|words|,|ex_words|)\n      if overlap > 0.7 → is_dup = True\n    if not is_dup: kept.append(span)"]
    B5 --> B6["extract_evidence() caps at max_spans=30"]
    B6 --> OUT["evidence: list[EvidenceSpan]\nstored in PRISMAReviewResult.evidence_spans[]"]
```

---

### Layer 2 — SQLite Cache

#### Schema

```sql
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,   -- SHA256 hex digest of "ns:ident"
    value      TEXT,               -- JSON blob
    created_at TEXT                -- ISO 8601 datetime string
);
```

#### Key derivation

```python
key = hashlib.sha256(f"{ns}:{ident}".encode()).hexdigest()
```

#### Cache read and write paths

```mermaid
flowchart TD
    subgraph READ["cache.get(ns, ident)"]
        R1["hash ns:ident → key"]
        R2["SELECT value, created_at\nWHERE key = ?"]
        R3{"row found?"}
        R4{"age > ttl\n72 hours?"}
        R5["DELETE FROM cache\nWHERE key = ?\nCOMMIT\nreturn None  cache miss"]
        R6["json.loads(value)\nreturn dict  cache hit"]
        R7["return None  cache miss"]

        R1 --> R2 --> R3
        R3 -->|no| R7
        R3 -->|yes| R4
        R4 -->|expired| R5
        R4 -->|fresh| R6
    end

    subgraph WRITE["cache.set(ns, ident, value: dict)"]
        W1["hash ns:ident → key"]
        W2["json.dumps(value) → json_str"]
        W3["INSERT OR REPLACE INTO cache\nVALUES key, json_str, now()\nCOMMIT\nresets TTL on re-fetch"]
        W1 --> W2 --> W3
    end
```

#### Namespace reference

| Namespace | Identifier | Stored value |
|---|---|---|
| `"search"` | `"{query}_{max}_{date_start}_{date_end}"` | `{"pmids": ["123", ...]}` |
| `"article"` | `"{pmid}"` | `Article.model_dump()` |
| `"related"` | `"{sorted_pmids_joined}"` | `{"pmids": [...]}` |
| `"fulltext"` | `"{pmc_id}"` e.g. `"PMC9876543"` | `{"text": "..."}` up to 12 000 chars |
| `"biorxiv"` | `"{query}_{days_back}"` | `{"articles": [Article.model_dump(), ...]}` |

#### Per-method cache flow

```mermaid
flowchart LR
    subgraph PM["PubMedClient"]
        PM1["search()\nns=search\nkey=query+max+dates"]
        PM2["fetch_articles()\nns=article\nkey=pmid\nbatch uncached 50"]
        PM3["find_related()\nns=related\nkey=sorted pmids"]
        PM4["fetch_full_text()\nns=fulltext\nkey=pmc_id\nmax 10 per call"]
    end

    subgraph BX["BioRxivClient"]
        BX1["search()\nns=biorxiv\nkey=query+days_back"]
    end

    subgraph C["Cache · SQLite"]
        CH["cache.get → hit/miss\ncache.set → store"]
    end

    PM1 <-->|check/store| C
    PM2 <-->|per-pmid check then batch MISS| C
    PM3 <-->|check/store| C
    PM4 <-->|per-pmc_id check/store| C
    BX1 <-->|check/store| C
```

#### TTL and expiry

- Default TTL: **72 hours** (`ttl_hours` param on `Cache.__init__`)
- Expiry is **lazy** — checked only on `get()`, no background vacuum
- Expired rows are deleted when first accessed after expiry
- `cache.clear()` → `DELETE FROM cache` — wipes all namespaces immediately
- Pass `enable_cache=False` to `PRISMAReviewPipeline` to skip cache entirely (`self.cache = None`; all `if self.cache:` guards in client methods are skipped)

---

### Layer 3 — Exported Files

`pipeline.run()` returns `PRISMAReviewResult`. The pipeline never writes files — that is the caller's responsibility. `main.py` writes to `prisma_results/`.

```mermaid
flowchart TD
    RES["PRISMAReviewResult returned\nfrom pipeline.run()"]

    RES --> MD_CHECK{"'md' in\nexport_formats?"}
    RES --> JSON_CHECK{"'json' in\nexport_formats?"}
    RES --> BIB_CHECK{"'bib' in\nexport_formats?"}

    MD_CHECK -->|yes| MD["to_markdown(result)\n→ prisma_results/{slug}.md"]
    JSON_CHECK -->|yes| JS["to_json(result)\n→ prisma_results/{slug}.json"]
    BIB_CHECK -->|yes| BT["to_bibtex(result)\n→ prisma_results/{slug}.bib"]

    subgraph MD_CONTENT["Markdown structure"]
        MC1["Abstract  objective · counts summary"]
        MC2["1. Introduction  rationale · PICO"]
        MC3["2. Methods  criteria · databases · queries · RoB tool"]
        MC4["3.1 PRISMA Flow table  all PRISMAFlowCounts fields"]
        MC5["3.2 Study Characteristics  author · year · design · RoB"]
        MC6["3.3 Synthesis  full LLM text"]
        MC7["3.4 Risk of Bias  bias_assessment text"]
        MC8["3.5 GRADE table  per-outcome certainty"]
        MC9["4. Limitations"]
        MC10["5. Other Info  registration · funding · conflicts"]
        MC11["References  numbered with DOI links"]
        MC12["Appendix  top 20 evidence spans + PMID + score"]
    end

    subgraph JSON_CONTENT["JSON  model_dump_json indent=2"]
        JC1["protocol  all ReviewProtocol fields"]
        JC2["flow  all PRISMAFlowCounts fields"]
        JC3["included_articles[]  full Article incl\nfull_text · risk_of_bias · extracted_data"]
        JC4["screening_log[]  every decision both stages"]
        JC5["evidence_spans[]  text · claim · relevance · pmid"]
        JC6["synthesis_text · bias_assessment · limitations"]
        JC7["grade_assessments{}  per outcome"]
    end

    subgraph BIB_CONTENT["BibTeX  @article per included study"]
        BC1["key = {FirstAuthorSurname}{Year}\nnon-alpha stripped"]
        BC2["title · author · journal\nyear · doi · pmid"]
    end

    MD --> MD_CONTENT
    JS --> JSON_CONTENT
    BT --> BIB_CONTENT
```

---

## PRISMA Flow Diagram — How It Works

`PRISMAFlowCounts` tracks article counts at every gate in the PRISMA 2020 flow diagram. This is how the pipeline populates each field.

```mermaid
flowchart TD
    subgraph IDENTIFICATION["IDENTIFICATION"]
        ID1["db_pubmed = count where source='pubmed_search'\nStep 2 end"]
        ID2["db_biorxiv = count where source='biorxiv'\nStep 3 end"]
        ID3["db_related = count where source='related_*'\nStep 4 end"]
        ID4["db_hops = count where source='hop_*'\nStep 5 end"]
        ID5["total_identified = len(all_articles)\nStep 5 end"]
    end

    subgraph SCREENING1["SCREENING — Deduplication"]
        SC1["duplicates_removed = total_identified − len(unique_map)\nStep 6"]
        SC2["after_dedup = len(deduped)\nStep 6"]
        SC3["screened_title_abstract = after_dedup\nStep 7 start"]
        SC4["excluded_title_abstract = len(ta_excluded)\nStep 7 end"]
    end

    subgraph SCREENING2["SCREENING — Full-text"]
        FT1["sought_fulltext = len(ta_included)\nStep 8 start"]
        FT2["not_retrieved = articles with no abstract AND no full_text\nStep 8 end"]
        FT3["assessed_eligibility = len(ta_included)\nStep 9 start"]
        FT4["excluded_eligibility = len(ft_excluded)\nStep 9 end"]
        FT5["excluded_reasons = top-8 reasons dict\nStep 9 end"]
    end

    subgraph INCLUDED["INCLUDED"]
        IN1["included_synthesis = len(ft_included)\nStep 9 end"]
    end

    IDENTIFICATION --> SCREENING1
    SCREENING1 --> SCREENING2
    SCREENING2 --> INCLUDED
```

### Deduplication key priority

```mermaid
flowchart LR
    A["Article a"] --> B{"a.doi\nnot empty?"}
    B -->|yes| C["key = a.doi.lower().strip()"]
    B -->|no| D["key = a.pmid"]
    C --> E{"key already\nin unique_map?"}
    D --> E
    E -->|no| F["unique_map[key] = a\narticle kept"]
    E -->|yes| G["skip\nduplicate removed"]
```

### Screening bias by stage

```mermaid
flowchart LR
    subgraph TA["Title/Abstract Stage · Step 7"]
        TA1["batch size = 15"]
        TA2["LLM instruction: INCLUSIVE\nwhen in doubt include"]
        TA3["failure → auto-include\nentire batch + log error"]
    end

    subgraph FT["Full-text Stage · Step 9"]
        FT1["batch size = 10"]
        FT2["LLM instruction: STRICT\nmust clearly satisfy all criteria"]
        FT3["no full_text available\n→ auto-include article"]
        FT4["failure → auto-include\nentire batch"]
    end

    TA -->|ta_included| FT
```

### Graceful degradation at each gate

```mermaid
flowchart TD
    G1["Step 7 screening batch raises exception\n→ auto-include all in batch\n→ log 'Auto-included error'"]
    G2["Step 9 screening batch raises exception\n→ auto-include all in batch"]
    G3["Step 10 evidence batch raises exception\n→ skip batch, continue\nother batches unaffected"]
    G4["Step 11 data extraction fails for article\n→ log failure\n→ article.extracted_data stays None"]
    G5["Step 12 RoB fails for article\n→ log failure\n→ article.risk_of_bias stays None"]
    G6["Step 14 asyncio.gather\nreturn_exceptions=True\nfailed tasks → empty string or missing key\nother tasks unaffected"]

    NOTE["pipeline.run() always returns\nPRISMAReviewResult\nno hard exits"]

    G1 & G2 & G3 & G4 & G5 & G6 --> NOTE
```

---

## Design Decisions

### 1. Agent-per-task, not a single mega-agent

Each PRISMA step that requires LLM reasoning has its own `Agent` with a dedicated system prompt, output model, and retry count. This gives independent prompt tuning, typed validated output (no string parsing), isolated retry logic, and easy replacement of any single step.

### 2. pydantic-ai for structured LLM output

All LLM outputs are Pydantic `BaseModel` subclasses. pydantic-ai handles parsing, automatic re-prompting on validation failure (`retries=2`), and typed return values with zero manual JSON handling.

### 3. `defer_model_check=True` — model injected at runtime

Agents are declared at module level without a model. The same agent instance works with any model the caller provides. Switching from Claude to GPT-4o to DeepSeek requires only the `model_name` argument.

### 4. OpenRouter as the single LLM gateway

`OpenRouterProvider` gives access to 100+ models through one API key. No vendor lock-in; cost and capability can be tuned per deployment without code changes.

### 5. Synchronous HTTP clients, async pipeline

`httpx.Client` (synchronous) is used in clients for simplicity — rate limiting via `time.sleep()` is straightforward. The pipeline is `async` to allow `asyncio.gather()` in step 14. If higher throughput is needed, replace with `httpx.AsyncClient`.

### 6. SQLite cache with 72-hour TTL

A local SQLite file caches all HTTP responses. Allows fast re-runs during development, offline re-analysis, and reduced NCBI rate-limit pressure. Cache is keyed by SHA256(namespace:identifier) — different query parameters produce different entries.

### 7. Two-stage screening with opposite biases

Title/abstract screening is inclusive (recall-optimised); full-text screening is strict (precision-optimised). This mirrors PRISMA best practice. Articles that pass title/abstract but have no retrievable full text are automatically forwarded.

### 8. Evidence deduplication by word overlap

Evidence spans are deduplicated using Jaccard-like word overlap at threshold 0.7. Removes near-identical paraphrases while keeping distinct claims. Threshold chosen empirically to catch paraphrases without removing legitimately similar but distinct evidence.

### 9. Parallel execution only where safe (step 14)

`asyncio.gather()` is used only in step 14 (bias summary, GRADE, limitations) because these tasks are fully independent of each other. All earlier steps are sequential because each depends on the previous step's output.

### 10. No hardcoded domain knowledge in prompts

System prompts contain methodological instructions but no field-specific content. All domain content (PICO, criteria, outcomes) comes from `ReviewProtocol` fields injected at call time via `@agent.system_prompt` context functions, making the pipeline domain-agnostic.

---

## Adding a New Agent

1. **Define the output model** in [models.py](models.py):
   ```python
   class MyOutput(BaseModel):
       result: str
       confidence: float = 0.5
   ```

2. **Declare the agent** in [agents.py](agents.py):
   ```python
   my_agent = Agent(
       output_type=MyOutput,
       deps_type=AgentDeps,
       system_prompt="You are a ...",
       retries=2,
       defer_model_check=True,
   )

   @my_agent.system_prompt
   async def _my_context(ctx: RunContext[AgentDeps]) -> str:
       return f"Research Question: {ctx.deps.protocol.question}"
   ```

3. **Write the runner**:
   ```python
   async def run_my_step(article: Article, deps: AgentDeps) -> MyOutput:
       model = build_model(deps.api_key, deps.model_name)
       result = await my_agent.run(
           f"Title: {article.title}\nAbstract: {article.abstract[:1000]}",
           deps=deps,
           model=model,
       )
       return result.output
   ```

4. **Call it in** [pipeline.py](pipeline.py) at the appropriate step; store result on the article or result object.

5. **Re-export** from `prisma_review_agent/__init__.py` if it is part of the public API.

---

## Environment & Configuration

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | Passed via `--api-key` CLI arg or directly to `PRISMAReviewPipeline` |
| `NCBI_API_KEY` | No | Enables 10 req/s vs 3 req/s at NCBI |

### Pipeline Constructor Parameters

```python
PRISMAReviewPipeline(
    api_key: str,               # OpenRouter API key (required)
    model_name: str,            # Default: "anthropic/claude-sonnet-4"
    ncbi_api_key: str,          # Default: "" (anonymous NCBI access)
    protocol: ReviewProtocol,   # Review protocol (required for useful results)
    enable_cache: bool,         # Default: True — SQLite cache on/off
    max_per_query: int,         # Default: 20 — max results per PubMed query
    related_depth: int,         # Default: 1 — rounds of related article expansion
    biorxiv_days: int,          # Default: 180 — bioRxiv lookback window in days
)
```

### RoB Tool Selection

Set `ReviewProtocol.rob_tool` to one of the `RoBTool` enum values. The agent's domain list is pulled from `ROB_DOMAINS` in [agents.py](agents.py):

| Tool | Study type |
|---|---|
| `RoBTool.ROB_2` | Randomised trials |
| `RoBTool.ROBINS_I` | Non-randomised interventions |
| `RoBTool.ROBINS_E` | Non-randomised exposures |
| `RoBTool.NOS` | Cohort / case-control |
| `RoBTool.QUADAS_2` | Diagnostic accuracy |
| `RoBTool.CASP` | Qualitative studies |
| `RoBTool.JBI` | Prevalence / cross-sectional |
| `RoBTool.MURAD` | Case reports / series |
| `RoBTool.SYRCLE` | Animal studies |
| `RoBTool.MINORS` | Non-randomised surgical |
| `RoBTool.ROBIS` | Systematic reviews |
| `RoBTool.JADAD` | Older RCT quality scale |
