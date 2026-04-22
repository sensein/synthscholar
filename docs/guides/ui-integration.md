# UI Integration — Compare Mode

This guide shows how to render **all the same tabs as non-compare mode** (Progress,
Synthesis, PRISMA Flow, Evidence, Screening, Studies, Charting, Appraisal,
Validation, Bias & GRADE, Export) **for every model** in a compare-mode run.

## TL;DR

The agent already returns the full per-model data. For each model `i`:

```
compare_result.model_results[i].result   # <- full PRISMAReviewResult, same as non-compare
```

If your UI is only showing `consensus_synthesis` and `synthesis_divergences`,
it's not reading `model_results[*].result` — it's an **integration gap**,
not a missing feature in the agent.

## Response Shape

A compare-mode run (`pipeline.run_compare(...)`) returns a `CompareReviewResult`.
Its JSON shape:

```json
{
  "protocol": { "title": "...", "inclusion_criteria": "...", "...": "..." },
  "compare_models": [
    "anthropic/claude-haiku-4-5",
    "google/gemini-3.1-flash-lite-preview",
    "openai/gpt-5.4-mini"
  ],
  "model_results": [
    {
      "model_name": "anthropic/claude-haiku-4-5",
      "result": {
        "protocol": { "...": "..." },
        "search_strategy": { "...": "..." },
        "prisma_flow": { "identified": 123, "screened": 98, "included": 45, "...": "..." },
        "included_articles": [ /* full Article objects with RoB, charting, appraisal, grade */ ],
        "evidence_spans": [ /* EvidenceSpan objects */ ],
        "data_charting_rubrics": [ /* DataChartingRubric per article */ ],
        "critical_appraisal_rubrics": [ /* CriticalAppraisalRubric per article */ ],
        "synthesis": { "synthesis_text": "...", "grade": { "...": "..." } },
        "bias_summary": "...",
        "limitations": "...",
        "grounding": { "verdict": "GROUNDED", "spans": [] },
        "prisma_document": { "abstract": {}, "introduction": {}, "...": "..." }
      },
      "error": null
    },
    { "model_name": "google/gemini-3.1-flash-lite-preview", "result": { "...": "..." }, "error": null },
    { "model_name": "openai/gpt-5.4-mini",                    "result": { "...": "..." }, "error": null }
  ],
  "merged": {
    "consensus_synthesis": "Across the systematic review...",
    "field_agreement": { "n_included": 0.87, "rob_overall": 0.72, "...": "..." },
    "synthesis_divergences": [
      { "topic": "Number of included studies...", "positions": { "openai/gpt-5.4-mini": "50 studies...", "...": "..." } }
    ]
  },
  "timestamp": "2026-04-22T10:34:12"
}
```

Everything you already render in non-compare mode lives inside
`model_results[i].result`. You just need to render it per model.

## FastAPI Endpoint

```python
from fastapi import FastAPI
from synthscholar import PRISMAReviewPipeline, ReviewProtocol

app = FastAPI()

@app.post("/review/compare")
async def compare(body: dict):
    protocol = ReviewProtocol(**body["protocol"])
    pipeline = PRISMAReviewPipeline(
        protocol=protocol,
        api_key="sk-or-...",
    )
    result = await pipeline.run_compare(
        models=body["models"],                       # e.g. ["anthropic/claude-haiku-4-5", ...]
        consensus_model=body.get("consensus_model"),
    )
    # model_dump() converts the full CompareReviewResult (with all per-model results)
    # into a JSON-serialisable dict.
    return result.model_dump(mode="json")
```

With SSE progress streaming:

```python
from fastapi.responses import StreamingResponse
import asyncio, json

@app.post("/review/compare/stream")
async def compare_stream(body: dict):
    protocol = ReviewProtocol(**body["protocol"])
    pipeline = PRISMAReviewPipeline(protocol=protocol, api_key="sk-or-...")

    async def events():
        q: asyncio.Queue = asyncio.Queue()

        def on_update(msg: str):
            q.put_nowait({"type": "progress", "message": msg})

        async def run():
            res = await pipeline.run_compare(
                models=body["models"],
                progress_callback=on_update,
            )
            q.put_nowait({"type": "done", "result": res.model_dump(mode="json")})
            q.put_nowait(None)

        asyncio.create_task(run())
        while True:
            item = await q.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
```

## UI Rendering Pattern

Two natural layouts work well:

### Pattern A — Model Selector + Existing Tabs

Add a **model selector** at the top. Switching models re-renders all the
existing non-compare tabs using that model's `result`.

```jsx
const [activeModel, setActiveModel] = useState(compare.compare_models[0]);

const current = compare.model_results.find(
  (r) => r.model_name === activeModel && r.result !== null,
)?.result;

return (
  <>
    <ModelSelector
      models={compare.compare_models}
      active={activeModel}
      onChange={setActiveModel}
      runs={compare.model_results}  // so selector can show ✓/✗ per model
    />
    <Tabs>
      <Tab name="Progress">      <ProgressView result={current} /> </Tab>
      <Tab name="Synthesis">     <SynthesisView result={current} merged={compare.merged} /> </Tab>
      <Tab name="PRISMA Flow">   <PrismaFlowView flow={current?.prisma_flow} /> </Tab>
      <Tab name="Evidence">      <EvidenceView spans={current?.evidence_spans} /> </Tab>
      <Tab name="Screening">     <ScreeningView articles={current?.included_articles} /> </Tab>
      <Tab name="Studies">       <StudiesView articles={current?.included_articles} /> </Tab>
      <Tab name="Charting">      <ChartingView rubrics={current?.data_charting_rubrics} /> </Tab>
      <Tab name="Appraisal">     <AppraisalView rubrics={current?.critical_appraisal_rubrics} /> </Tab>
      <Tab name="Validation">    <ValidationView grounding={current?.grounding} /> </Tab>
      <Tab name="Bias & GRADE">  <BiasGradeView result={current} /> </Tab>
      <Tab name="Export">        <ExportView compareResult={compare} /> </Tab>
    </Tabs>
  </>
);
```

This is the **least invasive** change: your existing per-tab components keep
working because they already consume a `PRISMAReviewResult` — you just feed
them `current` instead of the non-compare result.

### Pattern B — Side-by-Side Columns

For each tab, render N columns (one per model) showing that tab's view for
each model. Best for visual diffing but requires more layout work.

```jsx
<Tab name="PRISMA Flow">
  <div className="grid" style={{ gridTemplateColumns: `repeat(${compare.compare_models.length}, 1fr)` }}>
    {compare.model_results.map((run) => (
      <div key={run.model_name}>
        <h3>{run.model_name}</h3>
        {run.result
          ? <PrismaFlowView flow={run.result.prisma_flow} />
          : <ErrorBlock error={run.error} />}
      </div>
    ))}
  </div>
</Tab>
```

## Synthesis Tab (Recommended Layout)

The **Synthesis** tab should combine per-model synthesis + the existing
consensus/divergence block:

```jsx
<Tab name="Synthesis">
  {/* Per-model synthesis (same as non-compare) */}
  <section>
    <h3>{activeModel}</h3>
    <p>{current?.synthesis?.synthesis_text}</p>
  </section>

  {/* Consensus across all models */}
  <section>
    <h3>Consensus Synthesis</h3>
    <p>{compare.merged.consensus_synthesis}</p>
  </section>

  {/* Divergences */}
  <section>
    <h3>Synthesis Divergences</h3>
    {compare.merged.synthesis_divergences.map((d) => (
      <div key={d.topic}>
        <h4>{d.topic}</h4>
        <ul>
          {Object.entries(d.positions).map(([model, pos]) => (
            <li key={model}><b>{model}</b>: {pos}</li>
          ))}
        </ul>
      </div>
    ))}
  </section>
</Tab>
```

## Export Tab

Use the existing compare-mode exporters — they already handle per-model
sections and the merged consensus:

```python
from synthscholar import (
    to_compare_markdown,        # full PRISMA per model + consensus
    to_compare_json,            # complete structured JSON
    to_compare_charting_markdown,
    to_compare_charting_json,
)

@app.get("/review/compare/{review_id}/export/{fmt}")
async def export(review_id: str, fmt: str):
    result = load_compare_result(review_id)       # your storage
    if fmt == "md":   return PlainText(to_compare_markdown(result))
    if fmt == "json": return JSON(to_compare_json(result))
    ...
```

`to_compare_markdown` internally calls `to_markdown(run.result)` for each
successful model — so the exported file contains the full PRISMA document
for every model plus the consensus section.

## Checklist

If your compare-mode UI only shows model cards, consensus, and divergences
like the screenshot, verify:

- [ ] Your FastAPI endpoint returns `result.model_dump(mode="json")` — not a stripped subset
- [ ] The frontend state holds `compare_result.model_results` (array of `ModelReviewRun`)
- [ ] Each tab component reads from `model_results[i].result.<field>` (same fields as non-compare)
- [ ] A model selector exists OR side-by-side columns are rendered
- [ ] Failed runs (`run.error !== null`) are rendered with an error block, not omitted

Once `model_results[i].result` flows into your existing tab components,
compare mode has full parity with non-compare mode.
