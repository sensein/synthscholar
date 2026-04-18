"""Contract tests for Feature 008 Bug 3: assemble_prisma_review() timeout guard."""

import asyncio
import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestAssemblePrismaReviewSignature:
    def test_function_is_async_coroutine(self):
        from prisma_review_agent.pipeline import assemble_prisma_review
        assert inspect.iscoroutinefunction(assemble_prisma_review)

    def test_has_assemble_timeout_parameter(self):
        from prisma_review_agent.pipeline import assemble_prisma_review
        sig = inspect.signature(assemble_prisma_review)
        assert "assemble_timeout" in sig.parameters, (
            "assemble_prisma_review() must have an assemble_timeout parameter"
        )

    def test_assemble_timeout_default_is_3600(self):
        from prisma_review_agent.pipeline import assemble_prisma_review
        sig = inspect.signature(assemble_prisma_review)
        default = sig.parameters["assemble_timeout"].default
        assert default == 3600.0, f"Expected 3600.0, got {default}"

    def test_assemble_timeout_is_keyword_only_or_positional(self):
        from prisma_review_agent.pipeline import assemble_prisma_review
        sig = inspect.signature(assemble_prisma_review)
        param = sig.parameters["assemble_timeout"]
        # Must have a default (optional parameter)
        assert param.default != inspect.Parameter.empty


def _make_mock_result() -> MagicMock:
    r = MagicMock()
    r.data_charting_rubrics = []
    r.included_articles = []
    r.evidence_spans = []
    r.limitations = "None known"
    r.protocol = MagicMock()
    r.protocol.objective = "test objective"
    r.protocol.question = "test question"
    r.search_queries = []
    r.flow = MagicMock()
    r.bias_assessment = None
    r.structured_appraisal_results = None
    return r


class TestAssemblePrismaReviewWave1Timeout:
    async def test_wave1_hang_raises_timeout_error(self):
        from prisma_review_agent.pipeline import assemble_prisma_review

        async def _hanging(*args, **kwargs):
            await asyncio.sleep(9999)

        with (
            patch("prisma_review_agent.pipeline._assemble_methods", return_value=MagicMock()),
            patch("prisma_review_agent.pipeline._assemble_extracted_studies", return_value=[]),
            patch("prisma_review_agent.pipeline.run_thematic_synthesis", _hanging),
            patch("prisma_review_agent.pipeline.run_introduction_section", AsyncMock(return_value=MagicMock())),
            patch("prisma_review_agent.pipeline.run_quantitative_analysis", AsyncMock(return_value=None)),
        ):
            with pytest.raises(asyncio.TimeoutError):
                await assemble_prisma_review(
                    _make_mock_result(), MagicMock(), assemble_timeout=0.1
                )

    async def test_wave1_introduction_hang_raises_timeout_error(self):
        from prisma_review_agent.pipeline import assemble_prisma_review

        async def _hanging(*args, **kwargs):
            await asyncio.sleep(9999)

        with (
            patch("prisma_review_agent.pipeline._assemble_methods", return_value=MagicMock()),
            patch("prisma_review_agent.pipeline._assemble_extracted_studies", return_value=[]),
            patch("prisma_review_agent.pipeline.run_thematic_synthesis", AsyncMock(return_value=MagicMock())),
            patch("prisma_review_agent.pipeline.run_introduction_section", _hanging),
            patch("prisma_review_agent.pipeline.run_quantitative_analysis", AsyncMock(return_value=None)),
        ):
            with pytest.raises(asyncio.TimeoutError):
                await assemble_prisma_review(
                    _make_mock_result(), MagicMock(), assemble_timeout=0.1
                )


class TestAssemblePrismaReviewWave2Timeout:
    async def test_wave2_hang_raises_timeout_error(self):
        from prisma_review_agent.pipeline import assemble_prisma_review

        async def _hanging(*args, **kwargs):
            await asyncio.sleep(9999)

        mock_synthesis = MagicMock()
        mock_synthesis.themes = []
        mock_synthesis.paragraph_summary = ""
        mock_synthesis.question_answer_summary = None
        mock_synthesis.bias_assessment = MagicMock()
        mock_synthesis.bias_assessment.overall_quality = "Low"

        with (
            patch("prisma_review_agent.pipeline._assemble_methods", return_value=MagicMock()),
            patch("prisma_review_agent.pipeline._assemble_extracted_studies", return_value=[]),
            patch("prisma_review_agent.pipeline.run_thematic_synthesis", AsyncMock(return_value=mock_synthesis)),
            patch("prisma_review_agent.pipeline.run_introduction_section", AsyncMock(return_value=MagicMock())),
            patch("prisma_review_agent.pipeline.run_quantitative_analysis", AsyncMock(return_value=None)),
            patch("prisma_review_agent.pipeline.run_abstract_section", _hanging),
            patch("prisma_review_agent.pipeline.run_discussion_section", AsyncMock(return_value=MagicMock())),
            patch("prisma_review_agent.pipeline.run_conclusion_section", AsyncMock(return_value=MagicMock())),
        ):
            with pytest.raises(asyncio.TimeoutError):
                await assemble_prisma_review(
                    _make_mock_result(), MagicMock(), assemble_timeout=0.1
                )
