"""US2: Python API end-to-end tests via PRISMAReviewPipeline.run()."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from prisma_review_agent.models import PRISMAReviewResult
from prisma_review_agent.export import to_json, to_markdown


# ── Helpers ───────────────────────────────────────────────────────────────────

def _patch_http_clients():
    """Patch PubMed/BioRxiv synchronous search calls to return empty lists."""
    return [
        patch("prisma_review_agent.clients.PubMedClient.search", return_value=[]),
        patch("prisma_review_agent.clients.BioRxivClient.search", return_value=[]),
    ]


# ── e2e tests ─────────────────────────────────────────────────────────────────

@pytest.mark.e2e
async def test_pipeline_run_returns_valid_result(mock_pipeline):
    patches = _patch_http_clients()
    with patches[0], patches[1]:
        result = await mock_pipeline.run(auto_confirm=True)
    assert isinstance(result, PRISMAReviewResult)
    assert result.flow is not None
    assert isinstance(result.research_question, str)


@pytest.mark.e2e
async def test_pipeline_result_serialises_to_json(mock_pipeline):
    patches = _patch_http_clients()
    with patches[0], patches[1]:
        result = await mock_pipeline.run(auto_confirm=True)
    json_str = to_json(result)
    parsed = json.loads(json_str)
    assert isinstance(parsed, dict)


@pytest.mark.e2e
async def test_pipeline_to_markdown_has_sections(mock_pipeline):
    patches = _patch_http_clients()
    with patches[0], patches[1]:
        result = await mock_pipeline.run(auto_confirm=True)
    md = to_markdown(result)
    for section in ("Abstract", "Methods", "Results"):
        assert section in md, f"'{section}' not found in markdown"


@pytest.mark.e2e
async def test_pipeline_json_round_trip(mock_pipeline):
    patches = _patch_http_clients()
    with patches[0], patches[1]:
        result = await mock_pipeline.run(auto_confirm=True)
    json_str = to_json(result)
    restored = PRISMAReviewResult.model_validate_json(json_str)
    assert restored.research_question == result.research_question


@pytest.mark.integration
async def test_pipeline_cache_hit_on_second_run(minimal_protocol):
    pg_dsn = os.getenv("PRISMA_TEST_PG_DSN", "")
    if not pg_dsn:
        pytest.skip("PRISMA_TEST_PG_DSN not set")

    from pydantic_ai.models.test import TestModel
    from prisma_review_agent.pipeline import PRISMAReviewPipeline

    pipeline = PRISMAReviewPipeline(
        api_key="test-mock-key", model_name="test", pg_dsn=pg_dsn
    )
    pipeline.deps.model = TestModel()
    pipeline.protocol = minimal_protocol
    pipeline.deps.protocol = minimal_protocol

    patches = _patch_http_clients()
    with patches[0], patches[1]:
        await pipeline.run(auto_confirm=True)
        result2 = await pipeline.run(auto_confirm=True)

    assert result2.cache_hit is True


@pytest.mark.smoke
async def test_pipeline_smoke_real_api(minimal_protocol):
    if not os.getenv("RUN_E2E"):
        pytest.skip("RUN_E2E not set")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set")

    from prisma_review_agent.pipeline import PRISMAReviewPipeline

    pipeline = PRISMAReviewPipeline(api_key=api_key, protocol=minimal_protocol)
    result = await pipeline.run(auto_confirm=True)
    assert len(result.included_articles) >= 1
    assert result.synthesis_text
