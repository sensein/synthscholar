"""Shared fixtures for e2e tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prisma_review_agent.models import ReviewProtocol, PRISMAReviewResult
from prisma_review_agent.pipeline import PRISMAReviewPipeline


@pytest.fixture
def minimal_protocol() -> ReviewProtocol:
    return ReviewProtocol(
        title="CRISPR base editing in sickle cell disease",
        pico_population="Patients with sickle cell disease",
        pico_intervention="CRISPR base editing",
        pico_outcome="Efficacy and safety",
        databases=["PubMed"],
        max_hops=0,
        review_id="e2e-test-001",
    )


@pytest.fixture
def result_fixture() -> PRISMAReviewResult:
    path = Path(__file__).parent.parent / "fixtures" / "minimal_review_result.json"
    if not path.exists():
        pytest.skip("Fixture file not found — run scripts/build_e2e_fixture.py first")
    return PRISMAReviewResult.model_validate_json(path.read_text())


@pytest.fixture
def api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key and os.getenv("RUN_E2E"):
        pytest.skip("RUN_E2E set but OPENROUTER_API_KEY missing")
    return key or "test-mock-key"


@pytest.fixture
def mock_pipeline(api_key, minimal_protocol) -> PRISMAReviewPipeline:
    from pydantic_ai.models.test import TestModel
    pipeline = PRISMAReviewPipeline(api_key=api_key, model_name="test")
    pipeline.deps.model = TestModel()
    pipeline.protocol = minimal_protocol
    pipeline.deps.protocol = minimal_protocol
    return pipeline
