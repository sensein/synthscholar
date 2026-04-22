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

## Output Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--export`, `-e` | `FORMATS` | `md` | Space-separated: `md json bib ttl jsonld` |
| `--rdf-store-path` | `PATH` | — | Write pyoxigraph Turtle store to file |

## Mode Arguments

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--interactive`, `-i` | flag | off | Interactive protocol setup wizard |
| `--api-key` | `TEXT` | env var | OpenRouter API key (overrides `OPENROUTER_API_KEY`) |
| `--compare-models` | `MODEL [...]` | — | Run compare mode with 2+ model names |

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
