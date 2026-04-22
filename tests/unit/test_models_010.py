"""Unit tests for feature 010 — iterative large-review processing models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock, patch

from prisma_review_agent.cache.models import (
    PipelineCheckpoint,
    BatchMaxRetriesError,
)
from prisma_review_agent.models import ReviewProtocol


# ── PipelineCheckpoint ────────────────────────────────────────────────────────

class TestPipelineCheckpoint:
    def test_defaults(self):
        ckpt = PipelineCheckpoint(review_id="r1", stage_name="synthesis", batch_index=0)
        assert ckpt.status == "pending"
        assert ckpt.retries == 0
        assert ckpt.error_message == ""
        assert ckpt.result_json == {}
        assert ckpt.id == 0

    def test_valid_statuses(self):
        for status in ("pending", "in_progress", "complete", "failed"):
            ckpt = PipelineCheckpoint(
                review_id="r1", stage_name="synthesis", batch_index=0, status=status
            )
            assert ckpt.status == status

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            PipelineCheckpoint(
                review_id="r1", stage_name="synthesis", batch_index=0, status="unknown"
            )

    def test_review_id_required(self):
        with pytest.raises(ValidationError):
            PipelineCheckpoint(stage_name="synthesis", batch_index=0)  # type: ignore[call-arg]

    def test_result_json_stores_arbitrary_dict(self):
        payload = {"articles": [{"pmid": "12345", "title": "Test"}], "count": 1}
        ckpt = PipelineCheckpoint(
            review_id="r1", stage_name="charting", batch_index=2,
            status="complete", result_json=payload,
        )
        assert ckpt.result_json["count"] == 1
        assert ckpt.result_json["articles"][0]["pmid"] == "12345"

    def test_retries_default_zero(self):
        ckpt = PipelineCheckpoint(review_id="r1", stage_name="rob", batch_index=0)
        assert ckpt.retries == 0

    def test_error_message_stores_text(self):
        ckpt = PipelineCheckpoint(
            review_id="r1", stage_name="charting", batch_index=0,
            status="failed", error_message="Timeout after 30s", retries=1,
        )
        assert "Timeout" in ckpt.error_message
        assert ckpt.retries == 1


# ── BatchMaxRetriesError ──────────────────────────────────────────────────────

class TestBatchMaxRetriesError:
    def test_message_includes_stage_and_count(self):
        err = BatchMaxRetriesError("synthesis", 3, 3)
        assert "synthesis" in str(err)
        assert "3" in str(err)

    def test_attributes(self):
        err = BatchMaxRetriesError("charting", 5, 2)
        assert err.stage == "charting"
        assert err.batch_index == 5
        assert err.retries == 2

    def test_is_runtime_error(self):
        err = BatchMaxRetriesError("rob", 0, 1)
        assert isinstance(err, RuntimeError)


# ── ReviewProtocol new fields ─────────────────────────────────────────────────

class TestReviewProtocolIterativeFields:
    def test_defaults(self):
        proto = ReviewProtocol(title="T")
        assert proto.synthesis_batch_size == 20
        assert proto.max_batch_retries == 3

    def test_custom_values(self):
        proto = ReviewProtocol(title="T", synthesis_batch_size=10, max_batch_retries=5)
        assert proto.synthesis_batch_size == 10
        assert proto.max_batch_retries == 5

    def test_synthesis_batch_size_minimum_one(self):
        with pytest.raises(ValidationError):
            ReviewProtocol(title="T", synthesis_batch_size=0)

    def test_max_batch_retries_zero_allowed(self):
        proto = ReviewProtocol(title="T", max_batch_retries=0)
        assert proto.max_batch_retries == 0

    def test_max_batch_retries_negative_raises(self):
        with pytest.raises(ValidationError):
            ReviewProtocol(title="T", max_batch_retries=-1)

    def test_existing_fields_unchanged(self):
        proto = ReviewProtocol(title="My Review", objective="Test", synthesis_batch_size=15)
        assert proto.title == "My Review"
        assert proto.objective == "Test"
        assert proto.synthesis_batch_size == 15


# ── run_synthesis_merge_agent (mocked) ────────────────────────────────────────

class TestSynthesisMergeAgent:
    @pytest.mark.asyncio
    async def test_single_chunk_returns_without_merge(self):
        """With one partial synthesis, the merge agent is skipped."""
        from prisma_review_agent.agents import run_synthesis_merge_agent

        deps = MagicMock()
        result = await run_synthesis_merge_agent(["Only one chunk."], deps)
        assert result == "Only one chunk."

    @pytest.mark.asyncio
    async def test_empty_returns_empty_string(self):
        from prisma_review_agent.agents import run_synthesis_merge_agent

        deps = MagicMock()
        result = await run_synthesis_merge_agent([], deps)
        assert result == ""

    @pytest.mark.asyncio
    async def test_multiple_chunks_calls_agent(self):
        """With multiple chunks the merge agent is called and returns merged text."""
        from prisma_review_agent.agents import run_synthesis_merge_agent, _synthesis_merge_agent

        merged_text = "Merged synthesis across all chunks."
        mock_output = MagicMock()
        mock_output.output.synthesis_text = merged_text

        deps = MagicMock()
        deps.model = MagicMock()

        with patch.object(_synthesis_merge_agent, "run", new=AsyncMock(return_value=mock_output)):
            result = await run_synthesis_merge_agent(
                ["Chunk 1 text.", "Chunk 2 text.", "Chunk 3 text."],
                deps,
            )
        assert result == merged_text
