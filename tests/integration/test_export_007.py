"""Integration tests for Feature 007 compare export functions."""

import json
import pytest

from synthscholar.models import (
    CompareReviewResult,
    FieldAgreement,
    MergedReviewResult,
    ModelReviewRun,
    PRISMAReviewResult,
    ReviewProtocol,
    SynthesisDivergence,
)
from synthscholar.export import (
    to_compare_markdown,
    to_compare_json,
    to_compare_charting_markdown,
    to_compare_charting_json,
)


def _protocol() -> ReviewProtocol:
    return ReviewProtocol(title="CRISPR trials review")


def _result(synthesis: str = "Some synthesis text.") -> PRISMAReviewResult:
    return PRISMAReviewResult(
        research_question="CRISPR",
        protocol=_protocol(),
        synthesis_text=synthesis,
        timestamp="2026-01-01T00:00:00",
    )


def _compare(
    models=None,
    field_agreement=None,
    divergences=None,
    failed_model: str | None = None,
) -> CompareReviewResult:
    models = models or ["model-A", "model-B"]
    runs = []
    for m in models:
        if m == failed_model:
            runs.append(ModelReviewRun(model_name=m, error="simulated failure"))
        else:
            runs.append(ModelReviewRun(model_name=m, result=_result(f"Synthesis from {m}")))
    return CompareReviewResult(
        protocol=_protocol(),
        compare_models=[m for m in models if m != failed_model] + ([failed_model] if failed_model else []),
        model_results=runs,
        merged=MergedReviewResult(
            consensus_synthesis="Both models agree on efficacy.",
            field_agreement=field_agreement or {},
            synthesis_divergences=divergences or [],
        ),
    )


class TestToCompareMarkdown:
    def test_returns_string(self):
        result = to_compare_markdown(_compare())
        assert isinstance(result, str)
        assert len(result) > 0

    def test_contains_title(self):
        result = to_compare_markdown(_compare())
        assert "CRISPR trials review" in result

    def test_contains_model_headers(self):
        result = to_compare_markdown(_compare(["model-A", "model-B"]))
        assert "model-A" in result
        assert "model-B" in result

    def test_contains_run_summary_table(self):
        result = to_compare_markdown(_compare())
        assert "Run Summary" in result

    def test_contains_consensus_section(self):
        result = to_compare_markdown(_compare())
        assert "Consensus" in result
        assert "Both models agree on efficacy." in result

    def test_failed_model_shows_warning(self):
        result = to_compare_markdown(_compare(["A", "B"], failed_model="B"))
        assert "Failed" in result or "failed" in result or "⚠" in result

    def test_divergences_table_present(self):
        div = SynthesisDivergence(topic="accuracy", positions={"A": "high", "B": "low"})
        result = to_compare_markdown(_compare(divergences=[div]))
        assert "accuracy" in result
        assert "Notable Divergences" in result


class TestToCompareJson:
    def test_returns_valid_json(self):
        json_str = to_compare_json(_compare())
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_contains_compare_models_key(self):
        parsed = json.loads(to_compare_json(_compare(["A", "B"])))
        assert "compare_models" in parsed
        assert parsed["compare_models"] == ["A", "B"]

    def test_contains_merged_key(self):
        parsed = json.loads(to_compare_json(_compare()))
        assert "merged" in parsed

    def test_contains_model_results(self):
        parsed = json.loads(to_compare_json(_compare()))
        assert "model_results" in parsed
        assert len(parsed["model_results"]) == 2

    def test_round_trip(self):
        original = _compare()
        json_str = to_compare_json(original)
        restored = CompareReviewResult.model_validate_json(json_str)
        assert restored.compare_models == original.compare_models


class TestToCompareChartingMarkdown:
    def test_returns_string(self):
        result = to_compare_charting_markdown(_compare())
        assert isinstance(result, str)

    def test_no_field_answers_shows_fallback(self):
        result = to_compare_charting_markdown(_compare())
        assert "Charting Comparison" in result

    def test_agree_indicator_present(self):
        fa = {
            "R-001::sectionA::field1": FieldAgreement(
                field_name="field1", agreed=True, values={"A": "yes", "B": "yes"}
            )
        }
        result = to_compare_charting_markdown(_compare(field_agreement=fa))
        assert isinstance(result, str)

    def test_differ_indicator_present(self):
        fa = {
            "R-001::sectionA::field1": FieldAgreement(
                field_name="field1", agreed=False, values={"A": "yes", "B": "no"}
            )
        }
        result = to_compare_charting_markdown(_compare(field_agreement=fa))
        assert isinstance(result, str)

    def test_no_succeeded_runs_shows_no_data_message(self):
        compare = CompareReviewResult(
            protocol=_protocol(),
            compare_models=["A", "B"],
            model_results=[
                ModelReviewRun(model_name="A", error="fail"),
                ModelReviewRun(model_name="B", error="fail"),
            ],
            merged=MergedReviewResult(),
        )
        result = to_compare_charting_markdown(compare)
        assert "No successful" in result


class TestToCompareChartingJson:
    def test_returns_valid_json(self):
        json_str = to_compare_charting_json(_compare())
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_contains_compare_models(self):
        parsed = json.loads(to_compare_charting_json(_compare(["A", "B"])))
        assert "compare_models" in parsed
        assert parsed["compare_models"] == ["A", "B"]

    def test_contains_studies_list(self):
        parsed = json.loads(to_compare_charting_json(_compare()))
        assert "studies" in parsed
        assert isinstance(parsed["studies"], list)

    def test_field_agreement_in_output(self):
        fa = {
            "R-001::sectionA::field1": FieldAgreement(
                field_name="field1", agreed=True, values={"A": "yes", "B": "yes"}
            )
        }
        parsed = json.loads(to_compare_charting_json(_compare(field_agreement=fa)))
        assert len(parsed["studies"]) >= 1
        study = next(s for s in parsed["studies"] if s["source_id"] == "R-001")
        assert len(study["fields"]) == 1
        assert study["fields"][0]["agreed"] is True
