# CLI Reference

`synthscholar` is the command-line interface for running systematic reviews.

```bash
synthscholar [OPTIONS]
```

## Protocol Arguments

These define **what** to review.

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--title`, `-t` | `TEXT` | required | Research question / review title |
| `--objective` | `TEXT` | — | Detailed objective (expands PICO) |
| `--population` | `TEXT` | — | PICO: Population |
| `--intervention` | `TEXT` | — | PICO: Intervention |
| `--comparison` | `TEXT` | — | PICO: Comparison/control |
| `--outcome` | `TEXT` | — | PICO: Primary outcome |
| `--inclusion` | `TEXT` | — | Inclusion criteria (free text) |
| `--exclusion` | `TEXT` | — | Exclusion criteria (free text) |
| `--registration` | `TEXT` | — | PROSPERO registration number |

## Search Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--model`, `-m` | `TEXT` | `anthropic/claude-sonnet-4` | OpenRouter model name |
| `--databases` | `LIST` | `pubmed biorxiv` | Databases to search |
| `--max-results` | `INT` | `20` | Max results per query |
| `--related-depth` | `INT` | `1` | PubMed related-article expansion depth |
| `--hops` | `INT` | `10` | Citation graph hops (0–4) |
| `--biorxiv-days` | `INT` | `180` | bioRxiv lookback window in days |
| `--date-start` | `YYYY-MM-DD` | — | Earliest publication date |
| `--date-end` | `YYYY-MM-DD` | — | Latest publication date |
| `--rob-tool` | `CHOICE` | `RoB 2` | Risk of bias instrument (see below) |
| `--max-articles` | `INT` | — | Rerank + keep top N after dedup |

**`--rob-tool` choices:**
`RoB 2` · `ROBINS-I` · `ROBINS-E` · `Newcastle-Ottawa Scale` ·
`QUADAS-2` · `CASP Qualitative Checklist` · `JBI Critical Appraisal` ·
`Murad Tool` · `Jadad Scale`

## Pipeline Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--auto` | flag | off | Skip plan confirmation; run fully automated |
| `--extract-data` | flag | off | Enable per-study structured data extraction |
| `--concurrency` | `INT` | `5` | Parallel LLM calls per article (1–20) |
| `--max-plan-iterations` | `INT` | `3` | Max plan regeneration attempts |
| `--no-cache` | flag | off | Disable all caching |

## PostgreSQL Cache Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--pg-dsn` | `TEXT` | — | PostgreSQL connection string |
| `--force-refresh` | flag | off | Bypass cache, always recompute |
| `--cache-threshold` | `FLOAT` | `0.95` | Minimum similarity for a cache hit |
| `--cache-ttl-days` | `INT` | `30` | Cache time-to-live in days |

When `--pg-dsn` is set **and migration 005 is applied**, the full provenance trail (run config, plan iterations, search iterations, per-invocation telemetry) is persisted to a `review_telemetry` row keyed by `review_id`. See the [Provenance guide](guides/provenance.md) for the schema and SQL examples.

## Output Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--export`, `-e` | `FORMATS` | `md` | Space-separated: `md json bib ttl jsonld` |
| `--rdf-store-path` | `PATH` | — | Write pyoxigraph Turtle store to file |

## Mode Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--interactive`, `-i` | flag | off | Interactive protocol setup wizard |
| `--auto` | flag | off | Skip the interactive search-strategy confirmation |
| `--compare-models` | `MODEL [...]` | — | Run compare mode with 2+ model names |
| `--max-plan-iterations` | `INT` | `3` | Max plan regeneration attempts |

## Authentication Arguments

All five are also read from environment variables. **Precedence: explicit CLI flag > env var > built-in default.**

| Flag | Env var | Description |
|------|---------|-------------|
| `--api-key` | `OPENROUTER_API_KEY` | Required. OpenRouter key driving every LLM agent. |
| `--ncbi-api-key` | `NCBI_API_KEY` | Optional. Lifts PubMed E-utilities rate limit 3 → 10 req/s. |
| `--email` | `SYNTHSCHOLAR_EMAIL` | Optional. Polite-pool contact for OA providers (Unpaywall requires it). |
| `--semantic-scholar-key` | `SEMANTIC_SCHOLAR_API_KEY` | Optional. Higher-rate Semantic Scholar tier (DOI resolver). |
| `--core-key` | `CORE_API_KEY` | Optional. Required to enable the CORE OA-discovery leg. |

## Examples

**Basic automated review:**
```bash
synthscholar \
  --title "Deep learning in radiology" \
  --inclusion "CNN, diagnostic imaging, AUC reported" \
  --exclusion "non-English, conference abstracts" \
  --auto
```

**Full PICO with custom model and concurrency:**
```bash
synthscholar \
  --title "SGLT2 inhibitors in heart failure" \
  --population "adults with HFrEF" \
  --intervention "SGLT2 inhibitor" \
  --comparison "placebo" \
  --outcome "hospitalization, mortality" \
  --model openai/gpt-4o \
  --concurrency 10 \
  --export md json bib \
  --auto
```

**Compare mode:**
```bash
synthscholar \
  --title "..." \
  --inclusion "..." \
  --exclusion "..." \
  --compare-models anthropic/claude-sonnet-4 openai/gpt-4o \
  --auto \
  --export md json
```

**With PostgreSQL cache:**
```bash
synthscholar \
  --title "..." \
  --pg-dsn "postgresql://user:pass@localhost/reviews" \
  --cache-ttl-days 60 \
  --auto
```

**Interactive mode:**
```bash
synthscholar --interactive
```

---

# `synthscholar-search` — Corpus & Review Search

Once Postgres caching is enabled, every fetched article (with its full text where available) and every completed review is searchable. Two subcommands, three modes each.

```bash
synthscholar-search literature <query> [options]
synthscholar-search reviews    <query> [options]
```

## Modes (mutually exclusive)

| Flag | Subcommand | Description |
|------|-----------|-------------|
| _(default)_ | `literature` | Lexical FTS over title + abstract + full-text |
| `--by-title` | `literature` | Title-favouring lexical FTS variant |
| `--semantic` | both | pgvector cosine similarity (requires migration 004 + `[semantic]` extra) |

## Common Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--pg-dsn` | `TEXT` | — | PostgreSQL DSN (or set `PRISMA_PG_DSN`) |
| `--top` | `INT` | `20` | Max results to return |
| `--summarize` | flag | off | Feed top-K results through the synthesis agent for a stratified summary |
| `--summary-top` | `INT` | `15` | Articles fed to the synthesis agent when `--summarize` is set |
| `--api-key` | `TEXT` | env var | OpenRouter key (only required with `--summarize`) |
| `--model` | `TEXT` | `anthropic/claude-sonnet-4` | LLM used by `--summarize` |
| `--json` | flag | off | Emit JSON instead of human-readable output |

`reviews` adds `--include-expired` to surface entries past their TTL.

## Examples

```bash
# Lexical full-text search
synthscholar-search literature "GLP-1 obesity adolescents" --top 15

# Semantic search with stratified LLM summary
synthscholar-search literature "diagnostic accuracy speech" \
  --semantic --top 25 --summarize --summary-top 15

# Search past reviews and summarise across them
synthscholar-search reviews "hypertension" --semantic --summarize --json
```

`--summarize` auto-detects an informative grouping dimension (typically condition / disorder) and returns per-group aggregate findings with representative PMIDs — useful for landing-page result pages or "review of reviews" workflows. See the [Caching guide](guides/caching.md) for migrations and Python equivalents.
