#!/usr/bin/env python3
"""
Smoke test: verifies the full OpenRouter → plan confirmation → re-generation path.

Usage:
    export OPENROUTER_API_KEY="sk-or-v1-..."
    python scripts/smoke_test_confirmation.py

Exit codes:
    0 — PASS
    1 — FAIL (missing key or unexpected error)
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from synthscholar.models import ReviewProtocol, ReviewPlan
from synthscholar.pipeline import PRISMAReviewPipeline


def main() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("SKIP: OPENROUTER_API_KEY is not set — cannot run smoke test.")
        sys.exit(1)

    call_count = 0

    def confirm_callback(plan: ReviewPlan) -> "bool | str":
        nonlocal call_count
        call_count += 1
        print(f"[smoke] confirm_callback called (iteration {plan.iteration})")
        print(f"[smoke]   research_question: {plan.research_question[:80]}")
        print(f"[smoke]   pubmed_queries: {len(plan.pubmed_queries)}")
        if call_count == 1:
            print("[smoke] Returning feedback: 'add 2 more queries'")
            return "add 2 more queries"
        print("[smoke] Returning True (approved)")
        return True

    protocol = ReviewProtocol(
        title="Smoke test: CRISPR off-target effects",
        objective="Test plan confirmation re-generation",
    )

    pipeline = PRISMAReviewPipeline(
        api_key=api_key,
        protocol=protocol,
        enable_cache=False,
    )

    async def run() -> None:
        result = await pipeline.run(confirm_callback=confirm_callback, max_plan_iterations=3)
        print(f"[smoke] Pipeline completed — {len(result.included_articles)} articles included.")

    try:
        asyncio.run(run())
    except Exception as exc:
        print(f"FAIL: {exc}")
        sys.exit(1)

    if call_count < 2:
        print(f"FAIL: confirm_callback called only {call_count} time(s) — re-generation did not trigger.")
        sys.exit(1)

    print("PASS: re-generation succeeded")


if __name__ == "__main__":
    main()
