"""Unit tests for Feature 007 Pydantic models (multi-model compare mode)."""

import pytest
from pydantic import ValidationError

from prisma_review_agent.models import (
    CompareReviewResult,
    FieldAgreement,
    MergedReviewResult,
    ModelReviewRun,
    PRISMAFlowCounts,
    PRISMAReviewResult,
    ReviewProtocol,
    SynthesisDivergence,
)


def _minimal_result() -> PRISMAReviewResult:
    return PRISMAReviewResult(
        research_question="Test question",
        protocol=ReviewProtocol(title="Test"),
        flow=PRISMAFlowCounts(),
        synthesis_text="Test synthesis",
        timestamp="2026-04-17T00:00:00",
    )


def _minimal_compare_result(models: list[str] | None = None) -> CompareReviewResult:
    mods = models or ["model-A", "model-B"]
    runs = [ModelReviewRun(model_name=m, result=_minimal_result()) for m in mods]
    return CompareReviewResult(
        protocol=ReviewProtocol(title="Test"),
        compare_models=mods,
        model_results=runs,
        merged=MergedReviewResult(
            consensus_synthesis="Test consensus",
            models_included=mods,
        ),
        timestamp="2026-04-17T00:00:00",
    )


# ─── CompareReviewResult validators ────────────────────────────────────────────

class TestCompareReviewResult:
    def test_two_unique_models_ok(self):
        result = _minimal_compare_result(["model-A", "model-B"])
        assert len(result.compare_models) == 2

    def test_five_models_ok(self):
        models = [f"model-{i}" for i in range(5)]
        result = _minimal_compare_result(models)
        assert len(result.compare_models) == 5

    def test_fewer_than_two_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            CompareReviewResult(
                protocol=ReviewProtocol(title="T"),
                compare_models=["only-one"],
                model_results=[ModelReviewRun(model_name="only-one", result=_minimal_result())],
                merged=MergedReviewResult(),
                timestamp="",
            )

    def test_more_than_five_raises(self):
        models = [f"model-{i}" for i in range(6)]
        runs = [ModelReviewRun(model_name=m, result=_minimal_result()) for m in models]
        with pytest.raises((ValueError, ValidationError)):
            CompareReviewResult(
                protocol=ReviewProtocol(title="T"),
                compare_models=models,
                model_results=runs,
                merged=MergedReviewResult(),
                timestamp="",
            )

    def test_duplicate_models_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            CompareReviewResult(
                protocol=ReviewProtocol(title="T"),
                compare_models=["same", "same"],
                model_results=[
                    ModelReviewRun(model_name="same", result=_minimal_result()),
                    ModelReviewRun(model_name="same", result=_minimal_result()),
                ],
                merged=MergedReviewResult(),
                timestamp="",
            )


# ─── ModelReviewRun validators ─────────────────────────────────────────────────

class TestModelReviewRun:
    def test_result_only_ok(self):
        run = ModelReviewRun(model_name="m", result=_minimal_result())
        assert run.succeeded is True
        assert run.error is None

    def test_error_only_ok(self):
        run = ModelReviewRun(model_name="m", error="Something failed")
        assert run.succeeded is False
        assert run.result is None

    def test_neither_result_nor_error_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ModelReviewRun(model_name="m")

    def test_both_result_and_error_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ModelReviewRun(
                model_name="m",
                result=_minimal_result(),
                error="also an error",
            )

    def test_succeeded_property(self):
        ok = ModelReviewRun(model_name="m", result=_minimal_result())
        fail = ModelReviewRun(model_name="m", error="boom")
        assert ok.succeeded is True
        assert fail.succeeded is False


# ─── FieldAgreement values dict ───────────────────────────────────────────────

class TestFieldAgreement:
    def test_agreed_true(self):
        fa = FieldAgreement(
            field_name="Study Design",
            section_key="A",
            source_id="M-001",
            agreed=True,
            values={"model-A": "RCT", "model-B": "RCT"},
            answer_type="enumerated",
        )
        assert fa.agreed is True
        assert fa.values["model-A"] == "RCT"

    def test_agreed_false_different_values(self):
        fa = FieldAgreement(
            field_name="Study Design",
            section_key="A",
            source_id="M-001",
            agreed=False,
            values={"model-A": "RCT", "model-B": "Observational"},
            answer_type="enumerated",
        )
        assert fa.agreed is False

    def test_empty_values_dict_ok(self):
        fa = FieldAgreement(
            field_name="X", section_key="Y", source_id="Z",
            agreed=False,
        )
        assert fa.values == {}


# ─── MergedReviewResult JSON round-trip ───────────────────────────────────────

class TestMergedReviewResult:
    def test_json_round_trip(self):
        merged = MergedReviewResult(
            consensus_synthesis="Consensus text here",
            models_included=["model-A", "model-B"],
            models_failed=[],
            field_agreement={
                "M-001::A::Design": FieldAgreement(
                    field_name="Design", section_key="A", source_id="M-001",
                    agreed=True, values={"model-A": "RCT", "model-B": "RCT"},
                    answer_type="enumerated",
                )
            },
        )
        json_str = merged.model_dump_json()
        restored = MergedReviewResult.model_validate_json(json_str)
        assert restored.consensus_synthesis == merged.consensus_synthesis
        assert "M-001::A::Design" in restored.field_agreement
        assert restored.field_agreement["M-001::A::Design"].agreed is True


# ─── SynthesisDivergence min-2-positions validator ─────────────────────────────

class TestSynthesisDivergence:
    def test_two_positions_ok(self):
        sd = SynthesisDivergence(
            topic="Feature importance",
            positions={"model-A": "High importance", "model-B": "Not mentioned"},
        )
        assert len(sd.positions) == 2

    def test_three_positions_ok(self):
        sd = SynthesisDivergence(
            topic="Sample size",
            positions={"A": "n=50", "B": "n=100", "C": "Not reported"},
        )
        assert len(sd.positions) == 3

    def test_one_position_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            SynthesisDivergence(
                topic="X",
                positions={"model-A": "only one"},
            )

    def test_zero_positions_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            SynthesisDivergence(topic="X", positions={})
