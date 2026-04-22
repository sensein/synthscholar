# Quick Start

## CLI — One Command

```bash
synthscholar \
  --title "Machine learning for sepsis prediction in ICU" \
  --inclusion "adult ICU patients, ML/AI methods, mortality or sepsis outcome" \
  --exclusion "pediatric, reviews, non-English" \
  --export md json \
  --auto
```

`--auto` skips the interactive plan-confirmation step and runs end-to-end.

The output files are written to the current directory:

```
review_output.md
review_output.json
```

## Python API

```python
import asyncio
from synthscholar import PRISMAReviewPipeline, ReviewProtocol

protocol = ReviewProtocol(
    title="Machine learning for sepsis prediction in ICU",
    inclusion_criteria="adult ICU patients, ML/AI methods, mortality or sepsis outcome",
    exclusion_criteria="pediatric, reviews, non-English",
)

async def main():
    pipeline = PRISMAReviewPipeline(
        protocol=protocol,
        api_key="sk-or-...",        # or set OPENROUTER_API_KEY
        model_name="anthropic/claude-sonnet-4",
    )
    result = await pipeline.run()
    print(result.synthesis.synthesis_text)

asyncio.run(main())
```

## Compare Two Models

Run the same review with two LLMs and get field-level agreement scores:

```python
result = await pipeline.run_compare(
    models=["anthropic/claude-sonnet-4", "openai/gpt-4o"],
)
print(result.merged.consensus_synthesis)
for field, agreement in result.merged.field_agreement.items():
    print(f"{field}: {agreement:.0%} agreement")
```

Or via CLI:

```bash
synthscholar \
  --title "..." \
  --inclusion "..." \
  --exclusion "..." \
  --compare-models anthropic/claude-sonnet-4 openai/gpt-4o \
  --auto
```

## Export Formats

```python
from synthscholar import to_markdown, to_json, to_bibtex, to_turtle

md   = to_markdown(result)
js   = to_json(result)
bib  = to_bibtex(result)
ttl  = to_turtle(result)       # Turtle RDF
```

## Progress Streaming

Pass an `update_callback` to receive real-time pipeline updates:

```python
def on_update(message: str):
    print(f"[progress] {message}")

result = await pipeline.run(update_callback=on_update)
```

## Next Steps

- [CLI Reference](cli.md) — all flags explained
- [Compare Mode Guide](guides/compare-mode.md)
- [FastAPI Integration](guides/fastapi.md)
- [API Reference](api/index.md)
