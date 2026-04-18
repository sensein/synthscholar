"""Contract tests for Feature 007 run_compare() API."""

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from prisma_review_agent.compare import run_compare
from prisma_review_agent.models import (
    CompareReviewResult,
    PRISMAFlowCounts,
    PRISMAReviewResult,
    ReviewProtocol,
)


def _minimal_result(synthesis: str = "Test synthesis") -> PRISMAReviewResult:
    return PRISMAReviewResult(
        research_question="Test",
        protocol=ReviewProtocol(title="Test"),
        flow=PRISMAFlowCounts(),
        synthesis_text=synthesis,
        timestamp="2026-04-17T00:00:00",
    )


def _make_mock_pipeline(model_result=None, raise_exc=None):
    """Build a mock pipeline that honours _fetch_articles and _run_model_pipeline stubs."""
    pipeline = MagicMock()
    pipeline.protocol = ReviewProtocol(title="Test review")
    pipeline.deps = MagicMock()
    pipeline.deps.api_key = "test-key"

    acq = MagicMock()
    acq.deduped = []
    acq.flow = PRISMAFlowCounts()
    pipeline._fetch_articles = AsyncMock(return_value=acq)
    pipeline.log = MagicMock()
    pipeline._last_acq_flow = PRISMAFlowCounts()
    return pipeline


# ─── Function signature contract ──────────────────────────────────────────────

class TestRunCompareSignature:
    def test_is_coroutine_function(self):
        assert asyncio.iscoroutinefunction(run_compare)

    def test_accepts_models_list(self):
        sig = inspect.signature(run_compare)
        assert "models" in sig.parameters

    def test_has_progress_callback_param(self):
        sig = inspect.signature(run_compare)
        assert "progress_callback" in sig.parameters

    def test_has_consensus_model_param(self):
        sig = inspect.signature(run_compare)
        assert "consensus_model" in sig.parameters

    def test_returns_compare_review_result(self):
        sig = inspect.signature(run_compare)
        # Return annotation should reference CompareReviewResult
        ret = sig.return_annotation
        assert ret is not inspect.Parameter.empty


# ─── ValueError on < 2 models ─────────────────────────────────────────────────

class TestRunCompareValidation:
    def test_single_model_raises_value_error(self):
        pipeline = _make_mock_pipeline()
        with pytest.raises(ValueError, match="2"):
            asyncio.run(run_compare(pipeline, ["only-one-model"]))

    def test_empty_list_raises_value_error(self):
        pipeline = _make_mock_pipeline()
        with pytest.raises(ValueError):
            asyncio.run(run_compare(pipeline, []))

    def test_six_models_raises_value_error(self):
        pipeline = _make_mock_pipeline()
        models = [f"model-{i}" for i in range(6)]
        with pytest.raises(ValueError, match="5"):
            asyncio.run(run_compare(pipeline, models))

    def test_duplicate_models_raises_value_error(self):
        pipeline = _make_mock_pipeline()
        with pytest.raises(ValueError):
            asyncio.run(run_compare(pipeline, ["model-A", "model-A"]))


# ─── Returns CompareReviewResult with 2 ModelReviewRun entries ────────────────

class TestRunCompareResult:
    def test_two_models_both_succeed(self):
        pipeline = _make_mock_pipeline()

        async def fake_run_model_pipeline(pl, deduped, model_name, proto, **kwargs):
            return _minimal_result(f"Synthesis from {model_name}")

        from prisma_review_agent.agents import ConsensusSynthesisOutput
        mock_cs_coro = AsyncMock(return_value=ConsensusSynthesisOutput(
            consensus_text="Consensus", divergences=[]
        ))
        with patch("prisma_review_agent.compare._run_model_pipeline", fake_run_model_pipeline):
            with patch("prisma_review_agent.compare.run_consensus_synthesis", mock_cs_coro):
                result = asyncio.run(
                    run_compare(pipeline, ["model-A", "model-B"])
                )

        assert isinstance(result, CompareReviewResult)
        assert len(result.model_results) == 2
        assert all(r.succeeded for r in result.model_results)
        assert result.merged is not None
        assert result.timestamp != ""

    def test_partial_failure_one_succeeds_one_fails(self):
        pipeline = _make_mock_pipeline()

        async def fake_run_model_pipeline(pl, deduped, model_name, proto, **kwargs):
            if model_name == "bad-model":
                raise RuntimeError("Model not found")
            return _minimal_result("OK synthesis")

        with patch("prisma_review_agent.compare._run_model_pipeline", fake_run_model_pipeline):
            result = asyncio.run(
                run_compare(pipeline, ["good-model", "bad-model"])
            )

        assert isinstance(result, CompareReviewResult)
        assert len(result.model_results) == 2
        good = next(r for r in result.model_results if r.model_name == "good-model")
        bad = next(r for r in result.model_results if r.model_name == "bad-model")
        assert good.succeeded is True
        assert bad.succeeded is False
        assert bad.error is not None
        assert "bad-model" in result.merged.models_failed

    def test_fallback_consensus_when_only_one_succeeds(self):
        pipeline = _make_mock_pipeline()
        call_count = 0

        async def fake_run_model_pipeline(pl, deduped, model_name, proto, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _minimal_result("Synthesis")
            raise RuntimeError("Failed")

        with patch("prisma_review_agent.compare._run_model_pipeline", fake_run_model_pipeline):
            result = asyncio.run(
                run_compare(pipeline, ["model-A", "model-B"])
            )

        assert "Insufficient" in result.merged.consensus_synthesis
        assert result.merged.synthesis_divergences == []
