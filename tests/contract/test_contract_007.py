"""Contract tests for Feature 007: run_compare() API."""

import asyncio
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from synthscholar.models import (
    CompareReviewResult,
    MergedReviewResult,
    ModelReviewRun,
    PRISMAReviewResult,
    ReviewProtocol,
)


def _mock_result(question: str = "test") -> PRISMAReviewResult:
    return PRISMAReviewResult(
        research_question=question,
        protocol=ReviewProtocol(title="t"),
        timestamp="2026-01-01T00:00:00",
    )


def _mock_acq():
    from synthscholar.pipeline import AcquisitionResult, PRISMAFlowCounts
    return AcquisitionResult(
        deduped=[],
        all_search_queries=["q1"],
        flow=PRISMAFlowCounts(),
    )


class TestRunCompareSignature:
    def test_method_exists_on_pipeline(self):
        from synthscholar.pipeline import PRISMAReviewPipeline
        assert hasattr(PRISMAReviewPipeline, "run_compare")

    def test_is_async(self):
        from synthscholar.pipeline import PRISMAReviewPipeline
        assert inspect.iscoroutinefunction(PRISMAReviewPipeline.run_compare)

    def test_accepts_models_param(self):
        from synthscholar.pipeline import PRISMAReviewPipeline
        sig = inspect.signature(PRISMAReviewPipeline.run_compare)
        assert "models" in sig.parameters

    def test_has_consensus_model_param(self):
        from synthscholar.pipeline import PRISMAReviewPipeline
        sig = inspect.signature(PRISMAReviewPipeline.run_compare)
        assert "consensus_model" in sig.parameters

    def test_has_assemble_timeout_param(self):
        from synthscholar.pipeline import PRISMAReviewPipeline
        sig = inspect.signature(PRISMAReviewPipeline.run_compare)
        assert "assemble_timeout" in sig.parameters


class TestRunCompareValidation:
    async def test_fewer_than_2_models_raises_value_error(self):
        from synthscholar.compare import run_compare
        pipeline = MagicMock()
        pipeline.deps.api_key = "k"
        pipeline.protocol = ReviewProtocol(title="t")

        with pytest.raises(ValueError, match="at least 2"):
            await run_compare(pipeline, ["only-one"])

    async def test_duplicate_models_deduped_raises_value_error(self):
        from synthscholar.compare import run_compare
        pipeline = MagicMock()
        pipeline.deps.api_key = "k"
        pipeline.protocol = ReviewProtocol(title="t")

        with pytest.raises(ValueError, match="at least 2"):
            await run_compare(pipeline, ["A", "A"])

    async def test_more_than_5_models_raises_value_error(self):
        from synthscholar.compare import run_compare
        pipeline = MagicMock()
        pipeline.deps.api_key = "k"
        pipeline.protocol = ReviewProtocol(title="t")

        with pytest.raises(ValueError, match="at most 5"):
            await run_compare(pipeline, [f"m{i}" for i in range(6)])


class TestRunComparePartialFailure:
    async def test_one_model_fails_other_succeeds(self):
        from synthscholar.compare import run_compare

        good_result = _mock_result("good")

        async def _ok(*a, **kw):
            return good_result

        async def _fail(*a, **kw):
            raise RuntimeError("model exploded")

        call_count = 0

        async def _model_pipeline(pipeline, deduped, queries, flow, model_name, **kw):
            nonlocal call_count
            call_count += 1
            if model_name == "bad-model":
                raise RuntimeError("model exploded")
            return good_result

        pipeline = MagicMock()
        pipeline.deps.api_key = "k"
        pipeline.deps.model_name = "good-model"
        pipeline.protocol = ReviewProtocol(title="t")
        pipeline.log = MagicMock()
        pipeline._fetch_articles = AsyncMock(return_value=_mock_acq())

        with (
            patch("synthscholar.compare._run_model_pipeline", side_effect=_model_pipeline),
            patch("synthscholar.compare.run_consensus_synthesis", AsyncMock(
                return_value=MagicMock(consensus_text="ok", divergences=[])
            )),
        ):
            result = await run_compare(pipeline, ["good-model", "bad-model"])

        assert isinstance(result, CompareReviewResult)
        succeeded = [r for r in result.model_results if r.succeeded]
        failed = [r for r in result.model_results if not r.succeeded]
        assert len(succeeded) == 1
        assert len(failed) == 1
        assert failed[0].error is not None

    async def test_all_fail_returns_fallback_consensus(self):
        from synthscholar.compare import run_compare, _FALLBACK_CONSENSUS

        async def _fail(*a, **kw):
            raise RuntimeError("fail")

        pipeline = MagicMock()
        pipeline.deps.api_key = "k"
        pipeline.deps.model_name = "m1"
        pipeline.protocol = ReviewProtocol(title="t")
        pipeline.log = MagicMock()
        pipeline._fetch_articles = AsyncMock(return_value=_mock_acq())

        with patch("synthscholar.compare._run_model_pipeline", side_effect=_fail):
            result = await run_compare(pipeline, ["m1", "m2"])

        assert result.merged.consensus_synthesis == _FALLBACK_CONSENSUS


class TestRunCompareReturnsCorrectTypes:
    async def test_returns_compare_review_result(self):
        from synthscholar.compare import run_compare

        r = _mock_result()

        async def _ok(*a, **kw):
            return r

        pipeline = MagicMock()
        pipeline.deps.api_key = "k"
        pipeline.deps.model_name = "m1"
        pipeline.protocol = ReviewProtocol(title="t")
        pipeline.log = MagicMock()
        pipeline._fetch_articles = AsyncMock(return_value=_mock_acq())

        with (
            patch("synthscholar.compare._run_model_pipeline", side_effect=_ok),
            patch("synthscholar.compare.run_consensus_synthesis", AsyncMock(
                return_value=MagicMock(consensus_text="c", divergences=[])
            )),
        ):
            result = await run_compare(pipeline, ["m1", "m2"])

        assert isinstance(result, CompareReviewResult)
        assert len(result.model_results) == 2
        assert all(r.succeeded for r in result.model_results)
        assert isinstance(result.merged, MergedReviewResult)
