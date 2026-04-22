# Compare Mode

Compare mode runs the same systematic review protocol with **two or more LLMs in parallel**,
then measures where the models agree and diverge — giving you a cross-model confidence signal.

## When to Use It

- You want to validate findings across model families (e.g. Claude vs. GPT-4o)
- You need to report inter-rater agreement for a methodological paper
- You're evaluating which model produces higher-quality systematic reviews

## How It Works

```{raw} html
<ol class="pipeline-steps">
  <li>Article acquisition runs once (shared across all models)</li>
  <li>Each model runs its own independent pipeline in parallel</li>
  <li>Field-level agreement is computed for every structured output field</li>
  <li>A consensus synthesis LLM agent merges the per-model syntheses</li>
  <li>Divergences are surfaced as structured <code>SynthesisDivergence</code> items</li>
</ol>
```

## Python API

```python
import asyncio
from prisma_review_agent import PRISMAReviewPipeline, ReviewProtocol

protocol = ReviewProtocol(
    title="Machine learning for sepsis prediction in ICU",
    inclusion_criteria="adult ICU patients, ML/AI methods",
    exclusion_criteria="pediatric, reviews",
)

async def main():
    pipeline = PRISMAReviewPipeline(protocol=protocol, api_key="sk-or-...")

    result = await pipeline.run_compare(
        models=[
            "anthropic/claude-sonnet-4",
            "openai/gpt-4o",
        ],
        consensus_model="anthropic/claude-sonnet-4",  # model for consensus step
    )

    # Overall consensus text
    print(result.merged.consensus_synthesis)

    # Per-field agreement (0.0 – 1.0)
    for field, score in result.merged.field_agreement.items():
        print(f"  {field}: {score:.0%}")

    # Divergences
    for div in result.merged.synthesis_divergences:
        print(f"\nTopic: {div.topic}")
        for model, position in div.positions.items():
            print(f"  {model}: {position}")

asyncio.run(main())
```

## CLI

```bash
prisma-review \
  --title "Machine learning for sepsis prediction" \
  --inclusion "adult ICU patients, ML methods" \
  --exclusion "reviews, non-English" \
  --compare-models anthropic/claude-sonnet-4 openai/gpt-4o \
  --auto \
  --export md json
```

## Output Structure

Compare mode returns a `CompareReviewResult`:

```python
@dataclass
class CompareReviewResult:
    protocol: ReviewProtocol
    compare_models: list[str]
    model_results: list[ModelReviewRun]   # per-model full result or error
    merged: MergedReviewResult
```

```python
@dataclass
class MergedReviewResult:
    consensus_synthesis: str              # unified narrative
    field_agreement: dict[str, float]    # field → 0.0–1.0
    synthesis_divergences: list[SynthesisDivergence]
```

## Exporting Compare Results

```python
from prisma_review_agent import (
    to_compare_markdown, to_compare_json,
    to_compare_charting_markdown,
)

md  = to_compare_markdown(result)
js  = to_compare_json(result)
cht = to_compare_charting_markdown(result)
```

## Error Handling

If one model fails, compare mode records the error in `ModelReviewRun.error`
and continues with the remaining models. A consensus is only generated when
at least two models succeed.

```python
for run in result.model_results:
    if run.error:
        print(f"{run.model_name} failed: {run.error}")
    else:
        print(f"{run.model_name} succeeded")
```
