"""Unit tests for Feature 007 Pydantic models."""

import pytest
from pydantic import ValidationError

from prisma_review_agent.models import (
    CompareReviewResult,
    FieldAgreement,
    MergedReviewResult,
    ModelReviewRun,
    PRISMAReviewResult,
    ReviewProtocol,
    SynthesisDivergence,
)


def _protocol() -> ReviewProtocol:
    return ReviewProtocol(title="Test review")


def _result() -> PRISMAReviewResult:
    return PRISMAReviewResult(
        research_question="test",
        protocol=_protocol(),
        timestamp="2026-01-01T00:00:00",
    )


def _compare_result(models=None) -> CompareReviewResult:
    models = models or ["model-A", "model-B"]
    return CompareReviewResult(
        protocol=_protocol(),
        compare_models=models,
        model_results=[
            ModelReviewRun(model_name=m, result=_result()) for m in models
        ],
        merged=MergedReviewResult(),
    )


class TestFieldAgreement:
    def test_agreed_true(self):
        fa = FieldAgreement(field_name="f", agreed=True, values={"A": "yes", "B": "yes"})
        assert fa.agreed is True

    def test_agreed_false(self):
        fa = FieldAgreement(field_name="f", agreed=False, values={"A": "yes", "B": "no"})
        assert fa.agreed is False

    def test_values_preserves_model_names(self):
        fa = FieldAgreement(field_name="x", agreed=True, values={"modelA": "val1", "modelB": "val1"})
        assert set(fa.values.keys()) == {"modelA", "modelB"}


class TestSynthesisDivergence:
    def test_valid_two_positions(self):
        d = SynthesisDivergence(topic="accuracy", positions={"A": "high", "B": "moderate"})
        assert d.topic == "accuracy"

    def test_valid_three_positions(self):
        d = SynthesisDivergence(
            topic="t", positions={"A": "x", "B": "y", "C": "z"}
        )
        assert len(d.positions) == 3

    def test_one_position_raises(self):
        with pytest.raises(ValidationError):
            SynthesisDivergence(topic="t", positions={"A": "only one"})

    def test_zero_positions_raises(self):
        with pytest.raises(ValidationError):
            SynthesisDivergence(topic="t", positions={})


class TestModelReviewRun:
    def test_success_run(self):
        run = ModelReviewRun(model_name="m", result=_result())
        assert run.succeeded is True
        assert run.error is None

    def test_failed_run(self):
        run = ModelReviewRun(model_name="m", error="timeout")
        assert run.succeeded is False
        assert run.result is None

    def test_both_none_raises(self):
        with pytest.raises(ValidationError):
            ModelReviewRun(model_name="m")

    def test_both_set_raises(self):
        with pytest.raises(ValidationError):
            ModelReviewRun(model_name="m", result=_result(), error="oops")


class TestMergedReviewResult:
    def test_defaults(self):
        m = MergedReviewResult()
        assert m.consensus_synthesis == ""
        assert m.field_agreement == {}
        assert m.synthesis_divergences == []

    def test_json_round_trip(self):
        fa = FieldAgreement(field_name="f", agreed=True, values={"A": "v"})
        div = SynthesisDivergence(topic="t", positions={"A": "a", "B": "b"})
        m = MergedReviewResult(
            consensus_synthesis="consensus here",
            field_agreement={"s::k::f": fa},
            synthesis_divergences=[div],
        )
        json_str = m.model_dump_json()
        m2 = MergedReviewResult.model_validate_json(json_str)
        assert m2.consensus_synthesis == "consensus here"
        assert "s::k::f" in m2.field_agreement
        assert len(m2.synthesis_divergences) == 1


class TestCompareReviewResult:
    def test_valid_two_models(self):
        r = _compare_result(["A", "B"])
        assert r.compare_models == ["A", "B"]

    def test_valid_five_models(self):
        models = [f"m{i}" for i in range(5)]
        r = _compare_result(models)
        assert len(r.model_results) == 5

    def test_one_unique_model_raises(self):
        with pytest.raises(ValidationError):
            CompareReviewResult(
                protocol=_protocol(),
                compare_models=["only-one"],
                model_results=[ModelReviewRun(model_name="only-one", result=_result())],
                merged=MergedReviewResult(),
            )

    def test_duplicate_models_deduped_by_validator(self):
        with pytest.raises(ValidationError):
            CompareReviewResult(
                protocol=_protocol(),
                compare_models=["A", "A"],
                model_results=[
                    ModelReviewRun(model_name="A", result=_result()),
                    ModelReviewRun(model_name="A", result=_result()),
                ],
                merged=MergedReviewResult(),
            )
