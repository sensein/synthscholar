# Architecture

SynthScholar is an **async, agent-based pipeline** that orchestrates 24 specialised
`pydantic-ai` agents (16 in the core PRISMA path, 6 rich-synthesis section agents,
2 compare-mode merge agents), an OA full-text resolver chain (Europe PMC, Unpaywall,
OpenAlex, Semantic Scholar, PyMuPDF), article fetchers, caching, and exporters to
produce a fully structured PRISMA 2020 systematic review.

## High-Level Overview

```{mermaid}
flowchart LR
    U([User / CLI / FastAPI]) -->|ReviewProtocol| P[PRISMAReviewPipeline]

    subgraph Acquisition["📚 Article Acquisition"]
      direction TB
      SA[search_strategy_agent]
      PM[PubMed client]
      BX[bioRxiv client]
      REL[Related + citation hops]
      DEDUP[Dedup + rerank]
      SA --> PM
      SA --> BX
      PM --> REL
      BX --> REL
      REL --> DEDUP
    end

    subgraph PerArticle["🔬 Per-Article DAG (N articles concurrent, fused)"]
      direction TB
      SCREEN[Screening agent<br/>batched]
      EVID[Evidence extraction<br/>batched]
      subgraph DAG["per-article DAG"]
        direction LR
        ROB[Risk of Bias]
        DATA[Data extraction]
        CHART[Data Charting]
        APP[Critical Appraisal]
        NARR[Narrative rows]
        CHART --> APP --> NARR
      end
      SCREEN --> EVID
      EVID --> DAG
    end

    subgraph Synthesis["🧠 Synthesis Layer"]
      direction TB
      SYN[synthesis_agent<br/>parallel map-reduce<br/>+ run_synthesis_merge_agent]
      GV[Grounding validation]
      G1[Gather #1: bias + limitations<br/>+ intro + GRADE-per-outcome]
      G2[Gather #2: conclusions + abstract]
      THEM[Thematic synthesis<br/>map-reduce + structured merge]
      ASSEM[Assembly → PrismaReview]
      SYN --> GV --> G1 --> G2 --> THEM --> ASSEM
    end

    subgraph Output["📄 Exports"]
      MD[Markdown]
      JSON[JSON]
      BIB[BibTeX]
      TTL[Turtle / JSON-LD]
    end

    P --> Acquisition
    Acquisition --> PerArticle
    PerArticle --> Synthesis
    Synthesis --> Output

    PG[(PostgreSQL<br/>cache + checkpoints)] -.-> P
    OR[(OpenRouter LLM<br/>any provider)] -.-> PerArticle
    OR -.-> Synthesis
    OR -.-> SA

    classDef store fill:#e0f2fe,stroke:#0ea5e9,color:#0c4a6e;
    classDef agent fill:#eef2ff,stroke:#6366f1,color:#1e1b4b;
    class PG,OR store;
    class SA,SCREEN,EVID,ROB,CHART,APP,DATA,NARR,SYN,GV,G1,G2,THEM,ASSEM agent;
```

## Component View

```{image} _static/PRISMA_Agent_Architecture.png
:alt: SynthScholar architecture diagram
:align: center
:width: 100%
```

## Simplified View

```{image} _static/simplified_arch.png
:alt: Simplified architecture
:align: center
:width: 80%
```

## End-to-End Pipeline Flow

```{mermaid}
sequenceDiagram
    autonumber
    actor User
    participant API as FastAPI / CLI
    participant Pipe as PRISMAReviewPipeline
    participant LLM as OpenRouter
    participant Cache as PostgreSQL Cache
    participant Web as PubMed / bioRxiv

    User->>API: Submit ReviewProtocol
    API->>Pipe: run() / run_compare()

    Pipe->>Cache: lookup by protocol hash/similarity
    alt Cache hit (≥ 0.95 similarity)
        Cache-->>Pipe: cached PRISMAReviewResult
        Pipe-->>API: return cached
    else Cache miss
        Pipe->>LLM: search_strategy_agent (PICO → queries)
        LLM-->>Pipe: SearchStrategy
        Pipe-->>User: ReviewPlan (confirm?)
        User-->>Pipe: approve
        Pipe->>Web: PubMed + bioRxiv search
        Web-->>Pipe: articles
        Pipe->>Pipe: dedup + citation hops + rerank

        loop per batch (concurrency N)
            Pipe->>LLM: screening_agent (T/A — batches of 15)
            Pipe->>LLM: screening_agent (full-text — batches of 10)
            Pipe->>LLM: evidence_extraction_agent (batches of 5)
        end

        loop per article (DAG, all articles concurrent)
            par RoB, Extract, and the chain run as siblings
                Pipe->>LLM: rob_agent
            and
                Pipe->>LLM: data_extraction_agent (if data_items)
            and
                Pipe->>LLM: data_charting_agent
                Pipe->>LLM: critical_appraisal_agent
                Pipe->>LLM: narrative_row_agent
            end
        end

        Pipe->>LLM: synthesis_agent (parallel map-reduce if corpus > 80K chars)
        Pipe->>LLM: grounding_validation_agent
        par Gather #1
            Pipe->>LLM: bias_summary_agent
            Pipe->>LLM: limitations_agent
            Pipe->>LLM: introduction_agent
            Pipe->>LLM: grade_agent (one per PICO outcome)
        end
        par Gather #2
            Pipe->>LLM: conclusions_agent
            Pipe->>LLM: abstract_agent
        end
        Pipe->>LLM: thematic_synthesis_agent (map-reduce + structured merge)
        Pipe->>Pipe: assemble PrismaReview
        Pipe->>Cache: store result + per-stage checkpoints
        Pipe-->>API: PRISMAReviewResult
    end

    API-->>User: structured result (+ SSE progress)
```

## Compare Mode Flow

```{mermaid}
flowchart TB
    P[Protocol] --> ACQ[Shared article<br/>acquisition<br/>steps 1–6]

    ACQ --> M1[Model A pipeline<br/>steps 7–15]
    ACQ --> M2[Model B pipeline<br/>steps 7–15]
    ACQ --> M3[Model C pipeline<br/>steps 7–15]

    M1 --> R1[PRISMAReviewResult A]
    M2 --> R2[PRISMAReviewResult B]
    M3 --> R3[PRISMAReviewResult C]

    R1 --> FA[Field agreement<br/>computation]
    R2 --> FA
    R3 --> FA

    R1 --> CS[consensus_synthesis_agent]
    R2 --> CS
    R3 --> CS

    FA --> MERGE[MergedReviewResult]
    CS --> MERGE

    R1 --> OUT[CompareReviewResult]
    R2 --> OUT
    R3 --> OUT
    MERGE --> OUT

    classDef parallel fill:#fef3c7,stroke:#d97706,color:#78350f;
    class M1,M2,M3 parallel;
```

Article fetching runs **once** and is shared; every model then runs its own
independent per-article pipeline in parallel via `asyncio.gather`.

## Data Flow — Per-Article DAG

After title/abstract screening + evidence-span extraction, every included
article runs through a **fused per-article DAG**: three sibling legs that
fire concurrently within each article task, with all article tasks running
in parallel under a `proto.article_concurrency`-sized semaphore.

```{mermaid}
flowchart LR
    A[Article<br/>PMID/DOI/abstract/full_text] --> S[Screening<br/>INCLUDE/EXCLUDE]
    S -->|INCLUDE| E[Evidence spans<br/>grounded sentences]
    E --> DAG{{per-article DAG<br/>asyncio.gather}}
    DAG --> R[Risk of Bias]
    DAG --> D[Data extraction<br/>only if data_items]
    DAG --> CH[Data charting<br/>7-section rubric]
    CH --> AP[Critical appraisal<br/>4-domain rubric]
    AP --> NR[Narrative row<br/>6-cell summary]
    R & D & NR --> ART[Annotated Article]

    classDef extract fill:#dbeafe,stroke:#3b82f6;
    classDef dag fill:#fef3c7,stroke:#d97706,color:#78350f;
    class E,D,R,CH,AP,NR extract;
    class DAG dag;
```

Independent legs (RoB, Extract) start as soon as the article enters the DAG.
The Chart → Appraise → Narrate chain is internally sequential because each
step consumes the previous step's output. Eliminates the four corpus-wide
barriers the old block-by-block layout had between extract → RoB → chart →
appraise → narrate, so a slow article never holds up faster ones.

Every step produces typed Pydantic output backed by `retries=5` validation.

## Storage Schema

```{mermaid}
erDiagram
    review_cache ||--o{ pipeline_checkpoints : tracks
    review_cache {
        uuid id PK
        text protocol_hash
        text protocol_json
        jsonb result
        timestamptz created_at
        timestamptz expires_at
    }
    article_store {
        text pmid PK
        text doi
        text title
        text abstract
        text full_text
        tsvector ts
        timestamptz fetched_at
    }
    pipeline_checkpoints {
        uuid id PK
        uuid review_id FK
        text stage
        int batch_index
        jsonb partial_result
        timestamptz created_at
    }
```

See the [Caching guide](guides/caching.md) for how these tables are used.

## Retry & Validation

Every pydantic-ai agent is configured with:

- `output_type=<TypedModel>` — strict schema validation on each LLM response
- `retries=5` — if validation fails, pydantic-ai re-prompts up to 5 times with the error as feedback
- `defer_model_check=True` — model resolution happens at call time

```{mermaid}
flowchart LR
    CALL[agent.run] --> LLM[LLM call]
    LLM --> OUT[Raw JSON]
    OUT --> V{Pydantic<br/>validates?}
    V -->|✓| DONE[Typed result]
    V -->|✗| R{retry < 5?}
    R -->|yes| LLM
    R -->|no| ERR[ModelRetryError]
```

## Source Grounding

After evidence extraction, every `EvidenceSpan.text` is fuzzy-matched
(via `rapidfuzz`) against the source article's full text. The verdict
(`GROUNDED` / `PARTIALLY_GROUNDED` / `UNGROUNDED`) is attached to each span
and summarised in `GroundingValidationResult` on the final result.

This is the primary safeguard against LLM fabrication — spans that cannot
be located in the source are flagged and surfaced in the Validation tab.
