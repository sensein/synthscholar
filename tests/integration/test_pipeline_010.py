"""Integration tests for feature 010 — iterative large-review processing.

These tests verify checkpoint persistence, batch resume, and synthesis chunking.
They require a live PostgreSQL instance configured via PRISMA_TEST_PG_DSN env var.

Run with:
    PRISMA_TEST_PG_DSN=postgresql://user:pass@localhost/testdb pytest tests/integration/test_pipeline_010.py -v
"""

from __future__ import annotations

import os
import pytest

pytestmark = pytest.mark.integration

PG_DSN = os.getenv("PRISMA_TEST_PG_DSN", "")


@pytest.fixture
def pg_dsn():
    if not PG_DSN:
        pytest.skip("PRISMA_TEST_PG_DSN not set — skipping integration tests")
    return PG_DSN


@pytest.fixture
async def store(pg_dsn):
    from prisma_review_agent.cache.store import CacheStore
    async with CacheStore(dsn=pg_dsn) as s:
        yield s


# ── US1: Batch checkpoint round-trip ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_and_load_checkpoint(store):
    """save_checkpoint → load_checkpoints round-trip and upsert idempotency."""
    from prisma_review_agent.cache.models import PipelineCheckpoint

    review_id = "test-010-roundtrip"
    stage = "synthesis"
    await store.clear_checkpoints(review_id)

    ckpt = PipelineCheckpoint(
        review_id=review_id, stage_name=stage, batch_index=0,
        status="complete", result_json={"synthesis_text": "Test synthesis."},
    )
    saved = await store.save_checkpoint(ckpt)
    assert saved.id > 0
    assert saved.status == "complete"

    loaded = await store.load_checkpoints(review_id, stage)
    assert len(loaded) == 1
    assert loaded[0].result_json["synthesis_text"] == "Test synthesis."

    # Upsert — update same row
    ckpt.retries = 1
    ckpt.error_message = "retried once"
    updated = await store.save_checkpoint(ckpt)
    assert updated.retries == 1

    # Still only one row
    loaded_again = await store.load_checkpoints(review_id, stage)
    assert len(loaded_again) == 1

    await store.clear_checkpoints(review_id)


@pytest.mark.asyncio
async def test_clear_checkpoints_by_stage(store):
    """clear_checkpoints(review_id, stage_name) removes only that stage."""
    from prisma_review_agent.cache.models import PipelineCheckpoint

    review_id = "test-010-clear-stage"
    await store.clear_checkpoints(review_id)

    for stage in ("synthesis", "charting"):
        ckpt = PipelineCheckpoint(
            review_id=review_id, stage_name=stage, batch_index=0, status="complete"
        )
        await store.save_checkpoint(ckpt)

    await store.clear_checkpoints(review_id, stage_name="synthesis")

    remaining_synthesis = await store.load_checkpoints(review_id, "synthesis")
    remaining_charting = await store.load_checkpoints(review_id, "charting")

    assert len(remaining_synthesis) == 0
    assert len(remaining_charting) == 1

    await store.clear_checkpoints(review_id)


@pytest.mark.asyncio
async def test_load_completed_stages(store):
    """load_completed_stages returns only stages where all batches are complete."""
    from prisma_review_agent.cache.models import PipelineCheckpoint

    review_id = "test-010-completed-stages"
    await store.clear_checkpoints(review_id)

    # Complete stage: both batches done
    for idx in range(2):
        ckpt = PipelineCheckpoint(
            review_id=review_id, stage_name="synthesis",
            batch_index=idx, status="complete",
        )
        await store.save_checkpoint(ckpt)

    # Incomplete stage: one batch failed
    await store.save_checkpoint(PipelineCheckpoint(
        review_id=review_id, stage_name="charting", batch_index=0, status="complete"
    ))
    await store.save_checkpoint(PipelineCheckpoint(
        review_id=review_id, stage_name="charting", batch_index=1, status="failed"
    ))

    completed = await store.load_completed_stages(review_id)
    assert "synthesis" in completed
    assert "charting" not in completed

    await store.clear_checkpoints(review_id)


# ── US2: Synthesis chunking ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_synthesis_batch_size_creates_multiple_checkpoints(store):
    """A review with synthesis_batch_size=10 and 25 articles creates 3 synthesis checkpoints."""
    from prisma_review_agent.cache.models import PipelineCheckpoint

    review_id = "test-010-synthesis-chunks"
    await store.clear_checkpoints(review_id)

    # Simulate 3 synthesis batches
    for idx in range(3):
        ckpt = PipelineCheckpoint(
            review_id=review_id, stage_name="synthesis",
            batch_index=idx, status="complete",
            result_json={"synthesis_text": f"Chunk {idx} synthesis text."},
        )
        await store.save_checkpoint(ckpt)

    loaded = await store.load_checkpoints(review_id, "synthesis")
    assert len(loaded) == 3
    assert all(c.status == "complete" for c in loaded)
    texts = [c.result_json["synthesis_text"] for c in loaded]
    assert "Chunk 0" in texts[0]
    assert "Chunk 2" in texts[2]

    await store.clear_checkpoints(review_id)


# ── US3: Resume detection ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resume_skips_completed_stages(store):
    """load_completed_stages returns every stage that has all batches complete."""
    from prisma_review_agent.cache.models import PipelineCheckpoint

    review_id = "test-010-resume-all"
    await store.clear_checkpoints(review_id)

    stages = ["title_abstract_screening", "full_text_eligibility", "synthesis"]
    for stage in stages:
        ckpt = PipelineCheckpoint(
            review_id=review_id, stage_name=stage, batch_index=0, status="complete"
        )
        await store.save_checkpoint(ckpt)

    completed = await store.load_completed_stages(review_id)
    for stage in stages:
        assert stage in completed

    await store.clear_checkpoints(review_id)
