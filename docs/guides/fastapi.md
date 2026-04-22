# FastAPI Integration

This guide shows how to expose SynthScholar as an HTTP service, from a minimal
single-file app to production patterns with progress streaming, plan confirmation,
and polling.

## 1 — Minimal Complete App

A single-file `app.py` that you can copy-paste and run. Sync request/response
pattern — client makes one call, server runs the full review, returns the result.

```python
# app.py
import os
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from synthscholar import (
    PRISMAReviewPipeline,
    ReviewProtocol,
    CompareReviewResult,
    PRISMAReviewResult,
    to_markdown, to_json, to_bibtex,
    to_compare_markdown, to_compare_json,
)

app = FastAPI(title="SynthScholar API", version="0.2.9")

# Allow your UI origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request schema ──────────────────────────────────────────────────────────
class ProtocolRequest(BaseModel):
    title: str
    objective: str = ""
    population: str = ""
    intervention: str = ""
    comparison: str = ""
    outcome: str = ""
    inclusion_criteria: str
    exclusion_criteria: str
    registration: str = ""


class ReviewRequest(BaseModel):
    protocol: ProtocolRequest
    model: str = "anthropic/claude-sonnet-4"
    rob_tool: str = "RoB 2"
    max_results: int = 20
    concurrency: int = 5
    auto: bool = True


class CompareRequest(ReviewRequest):
    compare_models: list[str]            # ≥ 2 models
    consensus_model: str | None = None


# ── Single-model review ─────────────────────────────────────────────────────
@app.post("/review")
async def run_review(req: ReviewRequest) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENROUTER_API_KEY not set")

    pipeline = PRISMAReviewPipeline(
        protocol=ReviewProtocol(**req.protocol.model_dump()),
        api_key=api_key,
        model_name=req.model,
        rob_tool=req.rob_tool,
        max_results=req.max_results,
        concurrency=req.concurrency,
    )

    try:
        result: PRISMAReviewResult = await pipeline.run(auto=req.auto)
    except Exception as exc:
        raise HTTPException(500, f"Pipeline failed: {exc}") from exc

    # model_dump(mode="json") returns the full structured result — every field
    # the non-compare UI renders (prisma_flow, evidence, screening, charting,
    # appraisal, grounding, bias, GRADE, etc.) is already in this object.
    return result.model_dump(mode="json")


# ── Compare-mode review ─────────────────────────────────────────────────────
@app.post("/review/compare")
async def run_compare(req: CompareRequest) -> dict:
    if len(set(req.compare_models)) < 2:
        raise HTTPException(400, "compare_models must contain ≥ 2 unique models")

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise HTTPException(500, "OPENROUTER_API_KEY not set")

    pipeline = PRISMAReviewPipeline(
        protocol=ReviewProtocol(**req.protocol.model_dump()),
        api_key=api_key,
        rob_tool=req.rob_tool,
        max_results=req.max_results,
        concurrency=req.concurrency,
    )

    try:
        result: CompareReviewResult = await pipeline.run_compare(
            models=req.compare_models,
            consensus_model=req.consensus_model,
        )
    except Exception as exc:
        raise HTTPException(500, f"Compare run failed: {exc}") from exc

    # Each model's full PRISMAReviewResult lives at model_results[i].result —
    # your UI can render every non-compare tab per model using this data.
    return result.model_dump(mode="json")


# ── Exports ──────────────────────────────────────────────────────────────────
from fastapi.responses import PlainTextResponse

@app.post("/review/export/{fmt}")
async def export_review(fmt: Literal["md", "json", "bib"], req: ReviewRequest):
    pipeline = PRISMAReviewPipeline(
        protocol=ReviewProtocol(**req.protocol.model_dump()),
        api_key=os.environ["OPENROUTER_API_KEY"],
        model_name=req.model,
    )
    result = await pipeline.run(auto=True)

    if fmt == "md":   return PlainTextResponse(to_markdown(result), media_type="text/markdown")
    if fmt == "json": return PlainTextResponse(to_json(result), media_type="application/json")
    if fmt == "bib":  return PlainTextResponse(to_bibtex(result), media_type="application/x-bibtex")
```

Run it:

```bash
export OPENROUTER_API_KEY="sk-or-..."
pip install fastapi uvicorn synthscholar
uvicorn app:app --reload
```

Call it:

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{
    "protocol": {
      "title": "Machine learning for sepsis prediction",
      "inclusion_criteria": "adult ICU, ML methods, mortality outcome",
      "exclusion_criteria": "pediatric, reviews"
    },
    "model": "anthropic/claude-sonnet-4"
  }'
```

## 2 — Typed Python Client

For Python consumers, skip HTTP entirely and use the library directly:

```python
import asyncio
from synthscholar import PRISMAReviewPipeline, ReviewProtocol

async def main():
    protocol = ReviewProtocol(
        title="ML for sepsis prediction",
        inclusion_criteria="adult ICU, ML methods",
        exclusion_criteria="pediatric, reviews",
    )
    pipeline = PRISMAReviewPipeline(protocol=protocol, api_key="sk-or-...")

    # Option A — single model
    result = await pipeline.run(auto=True)
    print(f"Included: {len(result.included_articles)}")
    print(f"Synthesis: {result.synthesis.synthesis_text[:400]}")

    # Option B — compare mode
    compare = await pipeline.run_compare(
        models=["anthropic/claude-sonnet-4", "openai/gpt-4o"],
    )
    for run in compare.model_results:
        if run.succeeded:
            print(f"{run.model_name}: {len(run.result.included_articles)} articles")
    print("Consensus:", compare.merged.consensus_synthesis[:300])

asyncio.run(main())
```

## 3 — Server-Sent Events (progress streaming)

When a review takes minutes, stream progress to the browser instead of making
the client wait on one long HTTP request:

```python
from fastapi.responses import StreamingResponse
import asyncio, json

@app.post("/review/stream")
async def review_stream(req: ReviewRequest):
    pipeline = PRISMAReviewPipeline(
        protocol=ReviewProtocol(**req.protocol.model_dump()),
        api_key=os.environ["OPENROUTER_API_KEY"],
        model_name=req.model,
    )

    async def events():
        q: asyncio.Queue = asyncio.Queue()

        def on_update(msg: str):
            q.put_nowait({"type": "progress", "message": msg})

        async def run():
            try:
                result = await pipeline.run(update_callback=on_update, auto=True)
                q.put_nowait({"type": "done", "result": result.model_dump(mode="json")})
            except Exception as exc:
                q.put_nowait({"type": "error", "message": str(exc)})
            finally:
                q.put_nowait(None)

        asyncio.create_task(run())

        while True:
            item = await q.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
```

JavaScript client:

```js
const es = new EventSource("/review/stream", {
  method: "POST",
  body: JSON.stringify(protocol),
});
es.onmessage = (e) => {
  const data = JSON.parse(e.data);
  if (data.type === "progress") appendLog(data.message);
  if (data.type === "done")     { es.close(); renderResult(data.result); }
  if (data.type === "error")    { es.close(); showError(data.message); }
};
```

## 4 — Plan Confirmation (interactive)

When you don't pass `auto=True`, the pipeline pauses after generating a search
plan and waits for user approval. Use this pattern to wire it through HTTP:

```python
from synthscholar import ReviewPlan

pending_plans: dict[str, ReviewPlan] = {}
plan_decisions: dict[str, bool] = {}

@app.post("/review/interactive")
async def start_interactive(req: ReviewRequest):
    review_id = req.protocol.title[:40]    # use uuid in production

    pipeline = PRISMAReviewPipeline(
        protocol=ReviewProtocol(**req.protocol.model_dump()),
        api_key=os.environ["OPENROUTER_API_KEY"],
        model_name=req.model,
    )

    async def confirm(plan: ReviewPlan) -> bool:
        pending_plans[review_id] = plan
        for _ in range(300):           # wait up to 5 min
            await asyncio.sleep(1)
            if review_id in plan_decisions:
                return plan_decisions.pop(review_id)
        return False                    # timeout → reject

    result = await pipeline.run(plan_confirm_callback=confirm)
    return result.model_dump(mode="json")


@app.get("/plans/{review_id}")
def get_pending_plan(review_id: str):
    plan = pending_plans.get(review_id)
    if not plan:
        raise HTTPException(404, "No pending plan")
    return plan.model_dump(mode="json")


@app.post("/plans/{review_id}/decision")
def decide_plan(review_id: str, approved: bool):
    plan_decisions[review_id] = approved
    pending_plans.pop(review_id, None)
    return {"ok": True}
```

## 5 — Polling Fallback

For environments where SSE is blocked (some proxies, older mobile Safari),
kick the review off asynchronously and let clients poll:

```python
from uuid import uuid4

results: dict[str, dict] = {}

@app.post("/review/async")
async def start_async(req: ReviewRequest):
    review_id = str(uuid4())
    results[review_id] = {"status": "running"}

    async def run():
        pipeline = PRISMAReviewPipeline(
            protocol=ReviewProtocol(**req.protocol.model_dump()),
            api_key=os.environ["OPENROUTER_API_KEY"],
            model_name=req.model,
        )
        try:
            r = await pipeline.run(auto=True)
            results[review_id] = {"status": "done", "result": r.model_dump(mode="json")}
        except Exception as exc:
            results[review_id] = {"status": "error", "message": str(exc)}

    asyncio.create_task(run())
    return {"review_id": review_id}


@app.get("/review/async/{review_id}")
def poll(review_id: str):
    return results.get(review_id, {"status": "unknown"})
```

JavaScript:

```js
async function pollReview(id) {
  while (true) {
    const r = await fetch(`/review/async/${id}`).then(r => r.json());
    if (r.status === "done")  return r.result;
    if (r.status === "error") throw new Error(r.message);
    await new Promise(r => setTimeout(r, 2000));
  }
}
```

## 6 — Production Notes

- **API key management**: don't accept the OpenRouter key over HTTP — keep it in `OPENROUTER_API_KEY` on the server. Your frontend never sees it.
- **Timeouts**: reviews can take 5–30 minutes. Raise `uvicorn --timeout-keep-alive 1800` and your reverse-proxy timeouts accordingly, or use the polling pattern.
- **Concurrency**: `concurrency=5` ⇒ 5 parallel LLM calls per article-processing stage. Bump to 10–20 for larger reviews; costs scale linearly.
- **PostgreSQL caching**: pass `pg_dsn=` to `PRISMAReviewPipeline` and near-duplicate protocols served in seconds instead of minutes.
- **Background workers**: for heavy usage, move `pipeline.run()` off the request thread into Celery / RQ / arq and keep FastAPI just for submission + status.
- **CORS + auth**: scope `CORSMiddleware` tightly, and put this behind your own auth layer (JWT, session, API key).
