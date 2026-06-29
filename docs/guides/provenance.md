# Provenance & Process Audit

Every SynthScholar review records *how* the analysis was produced — not just what. This guide explains what is captured, where it lives, and how to read it.

## What is captured

Eight categories of provenance ride on every `PRISMAReviewResult`:

| Category | Field on result | What it answers |
|---|---|---|
| **Run configuration** | `run_configuration` | Which model, which CLI flags, which env vars were present (values **never** stored), package version, started-at timestamp. |
| **Plan iterations** | `plan_iterations` | Every plan the search-strategy agent produced, the operator's feedback, the decision (`approved` / `rejected` / `feedback` / `auto_confirmed`). |
| **Search iterations** | `search_iterations` | Every PubMed/bioRxiv/medRxiv query, each citation-hop expansion, each related-articles round — with seeds, new PMIDs, durations. |
| **Agent invocations** | `agent_invocations` | One record per `agent.run()` call: step name, iteration mode, model, tokens, retries, tool-call summary, target PMID/outcome, prompt snapshot, success flag. |
| **Source hashes** | `Article.content_sha256` | SHA-256 of every parsed full-text body, for reproducibility checks. |
| **Per-publication source** | `Article.source` (also `DataChartingRubric.database_retrieved`, `SourceMetadata.database_retrieved_from`) | Which database each article was identified from: `pubmed_search`, `biorxiv`, `medrxiv`, `related_{N}`, `hop_{N}`, or an OA-provider name (`openalex`, `europepmc`, `crossref`, `doaj`, `semanticscholar`). |
| **Per-database PRISMA numbers** | `flow.db_pubmed` / `db_biorxiv` / `db_medrxiv` / `db_related` / `db_hops` / `db_other_sources` | The PRISMA 2020 Item 16a identification breakdown — sum equals `flow.total_identified`. `db_other_sources` is a `dict[str, int]` covering any source not in the named set. |
| **Iteration mode** | per-invocation tag | Distinguishes `zero_shot` (single call) from `iterative_with_human_feedback`, `iterative_with_fallback`, `iterative_expansion`, `hierarchical_reduce`, `self_check_retry`, `validated_against_source`. |

## Why this matters

The classic question is: *were the search queries any good, or did the LLM just guess once?* SynthScholar's answer is auditable:

- The **plan-iteration history** records every query version the operator saw, the feedback they gave, and the regeneration that resulted. Queries are explicitly **not** zero-shot — they are iterated against human review until the operator approves.
- The **iteration-mode tag** on every other agent invocation tells you whether that step ran once, fell back through providers, expanded seeds, or sharded-and-merged a large corpus.

## Where it lives

**Always (in-memory + JSON export):** `result.run_configuration`, `result.plan_iterations`, `result.agent_invocations`, `result.search_iterations`, and `Article.content_sha256` are returned on every `PRISMAReviewResult` and serialized to JSON via `to_json(result)`.

**Markdown export:** every `to_markdown(result)` ends with a **Provenance — How This Analysis Was Produced** section showing run configuration, iterative-vs-zero-shot phase summary, plan-iteration history with operator feedback, search-iteration trail, and per-invocation token / latency telemetry.

**Turtle / JSON-LD export:** `to_turtle(result)` / `to_jsonld(result)` emit:

- `slr:RunConfiguration` linked from the review via `prov:used`
- `slr:PlanIteration` chain (each plan a `prov:Entity`, linked by `prov:wasRevisionOf` to the previous version)
- `slr:SearchIteration` activities with `prov:startedAtTime` and discovered PMIDs
- `slr:AgentInvocation` activities (each a `prov:Activity`) with `slr:iteration_mode`, model, tokens, requests, tool-call summary
- `slr:content_sha256` on every included article

**PostgreSQL (full audit trail):** when `--pg-dsn` is set **and migration 005 is applied**, the complete provenance trail is persisted to a `review_telemetry` row keyed by `review_id`:

```sql
psql "$PRISMA_PG_DSN" -f synthscholar/cache/migrations/005_add_review_telemetry.sql
```

The table holds JSONB columns for each provenance kind plus pre-computed totals (`n_invocations`, `total_input_tokens`, `total_output_tokens`, `n_plan_iterations`). Without migration 005 the JSON / Markdown / Turtle exports still carry everything — Postgres is the optional audit-grade store.

## Iteration modes (reference)

| Mode | When you'll see it | Example phases |
|---|---|---|
| `zero_shot` | Single LLM call, no loop | Screening, RoB, charting, appraisal, narrative rows, GRADE per outcome, abstract / introduction / conclusion sections |
| `iterative_with_human_feedback` | Plan regen until operator approves | Search-strategy generation |
| `iterative_with_fallback` | Cascade through providers until one succeeds | Full-text resolver chain (PMC → Europe PMC OA-XML → preprint PDF → Unpaywall → OpenAlex → Semantic Scholar) |
| `iterative_expansion` | Output of round N seeds round N+1 | Citation-hop search, related-articles depth expansion |
| `hierarchical_reduce` | Shard → partial → merge | Map-reduce synthesis, thematic synthesis, consensus synthesis (compare-mode) |
| `self_check_retry` | Schema-violation retry loop only — no semantic feedback | `_run_with_text_fallback` for schema-too-complex provider errors |
| `validated_against_source` | Zero-shot, then deterministic post-filter | Evidence extraction (LLM extract → `validation.filter_grounded` fuzzy match drops ungrounded), grounding validation |

## Privacy note — API keys are NEVER stored

`run_configuration.env_vars_present` is a `dict[str, bool]` recording **only the presence** of `OPENROUTER_API_KEY`, `NCBI_API_KEY`, `SYNTHSCHOLAR_EMAIL`, `SEMANTIC_SCHOLAR_API_KEY`, `CORE_API_KEY`, `PRISMA_PG_DSN`. The values themselves never enter the result, the JSON export, the Markdown export, the Turtle graph, or the Postgres telemetry table.

## Reading the provenance trail in Python

```python
result = await pipeline.run(auto_confirm=True)

print(f"{len(result.plan_iterations)} plan iterations")
for pi in result.plan_iterations:
    print(f"  iter {pi.iteration_index}: {pi.decision} — {pi.user_feedback[:60]}")

# Iterative-vs-zero-shot summary
from collections import Counter
modes = Counter(i.iteration_mode for i in result.agent_invocations)
print(modes)
# Counter({'zero_shot': 87, 'hierarchical_reduce': 4,
#          'iterative_with_human_feedback': 1, 'validated_against_source': 8})

total_in = sum(i.input_tokens for i in result.agent_invocations)
total_out = sum(i.output_tokens for i in result.agent_invocations)
print(f"{total_in:,} input + {total_out:,} output tokens across "
      f"{len(result.agent_invocations)} invocations")
```

## Loading a past run's telemetry from Postgres

```python
from synthscholar.cache.store import CacheStore

store = CacheStore(dsn="postgresql://...")
await store.connect()
trail = await store.load_telemetry(review_id="my-review-2026-04-29")
print(trail["n_invocations"], "invocations,",
      trail["total_input_tokens"], "input tokens")
print(trail["plan_iterations"])  # JSONB → list[dict]
await store.close()
```
