#!/usr/bin/env python3
"""Build the e2e test fixture: tests/fixtures/minimal_review_result.json.

Requires OPENROUTER_API_KEY to be set.  Run once and commit the output.
Refresh whenever PRISMAReviewResult schema changes.

Usage:
    export OPENROUTER_API_KEY="sk-..."
    python scripts/build_e2e_fixture.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from synthscholar.models import ReviewProtocol
from synthscholar.pipeline import PRISMAReviewPipeline
from synthscholar.export import to_json


async def main() -> None:
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        print("ERROR: OPENROUTER_API_KEY is not set.", file=sys.stderr)
        sys.exit(1)

    protocol = ReviewProtocol(
        title="CRISPR base editing in sickle cell disease",
        pico_population="Patients with sickle cell disease",
        pico_intervention="CRISPR base editing",
        pico_outcome="Efficacy and safety",
        databases=["PubMed"],
        max_hops=0,
        review_id="e2e-test-001",
    )

    print(f"Running pipeline for: {protocol.title}")
    print("This may take several minutes …\n")

    pipeline = PRISMAReviewPipeline(
        api_key=api_key,
        protocol=protocol,
        max_per_query=10,
    )

    result = await pipeline.run(auto_confirm=True)

    out_path = ROOT / "tests" / "fixtures" / "minimal_review_result.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(to_json(result), encoding="utf-8")

    print(f"\nFixture written to: {out_path}")
    print(f"Included articles: {len(result.included_articles)}")
    print("Commit this file to keep the export tests self-contained.")


if __name__ == "__main__":
    asyncio.run(main())
