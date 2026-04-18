"""Integration tests for Feature 007 compare export functions."""

import json

from prisma_review_agent.export import (
    to_compare_charting_json,
    to_compare_charting_markdown,
    to_compare_json,
    to_compare_markdown,
)
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


def _result(synthesis: str = "Test synthesis") -> PRISMAReviewResult:
    return PRISMAReviewResult(
        research_question="ALS voice biomarkers",
        protocol=ReviewProtocol(title="ALS Review"),
        flow=PRISMAFlowCounts(total_identified=50, included_synthesis=5),
        synthesis_text=synthesis,
        timestamp="2026-04-17T00:00:00",
    )


def _field_agreement(
    agreed: bool,
    model_a_val: str,
    model_b_val: str,
    field_name: str = "Feature Importance Reported",
) -> FieldAgreement:
    return FieldAgreement(
        field_name=field_name,
        section_key="F",
        source_id="M-001",
        agreed=agreed,
        values={"model-A": model_a_val, "model-B": model_b_val},
        answer_type="yes_no_extended",
    )


def _compare_result(
    *,
    include_field_agreement: bool = True,
    include_divergences: bool = True,
    model_b_failed: bool = False,
) -> CompareReviewResult:
    run_b = (
        ModelReviewRun(model_name="model-B", error="Failed for testing")
        if model_b_failed
        else ModelReviewRun(model_name="model-B", result=_result("Model B synthesis"))
    )
    fa = {}
    if include_field_agreement:
        fa["M-001::F::Feature Importance Reported"] = _field_agreement(True, "Yes", "Yes")
        fa["M-001::F::Top Features"] = _field_agreement(
            False, "MFCCs, jitter", "MFCCs, shimmer", field_name="Top Features"
        )

    divs = []
    if include_divergences:
        divs = [
            SynthesisDivergence(
                topic="Feature importance",
                positions={"model-A": "MFCCs most important", "model-B": "Jitter more important"},
            )
        ]

    return CompareReviewResult(
        protocol=ReviewProtocol(title="ALS Review"),
        compare_models=["model-A", "model-B"],
        model_results=[
            ModelReviewRun(model_name="model-A", result=_result("Model A synthesis")),
            run_b,
        ],
        merged=MergedReviewResult(
            consensus_synthesis="Both models agree on MFCCs as key feature.",
            field_agreement=fa,
            synthesis_divergences=divs,
            models_included=["model-A"] if model_b_failed else ["model-A", "model-B"],
            models_failed=["model-B"] if model_b_failed else [],
        ),
        timestamp="2026-04-17T00:00:00",
    )


# ─── to_compare_markdown ──────────────────────────────────────────────────────

class TestToCompareMarkdown:
    def test_produces_non_empty_string(self):
        result = _compare_result()
        md = to_compare_markdown(result)
        assert isinstance(md, str)
        assert len(md) > 100

    def test_contains_title_heading(self):
        result = _compare_result()
        md = to_compare_markdown(result)
        assert "ALS Review" in md
        assert "Multi-Model Compare" in md

    def test_contains_run_summary_table(self):
        result = _compare_result()
        md = to_compare_markdown(result)
        assert "Run Summary" in md
        assert "model-A" in md
        assert "model-B" in md

    def test_failed_model_shows_warning(self):
        result = _compare_result(model_b_failed=True)
        md = to_compare_markdown(result)
        assert "Failed" in md or "⚠" in md

    def test_merged_section_present(self):
        result = _compare_result()
        md = to_compare_markdown(result)
        assert "Merged" in md
        assert "Consensus" in md

    def test_divergences_table_present(self):
        result = _compare_result(include_divergences=True)
        md = to_compare_markdown(result)
        assert "Divergences" in md
        assert "Feature importance" in md


# ─── to_compare_json ──────────────────────────────────────────────────────────

class TestToCompareJson:
    def test_produces_valid_json(self):
        result = _compare_result()
        json_str = to_compare_json(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_json_round_trip(self):
        result = _compare_result()
        json_str = to_compare_json(result)
        restored = CompareReviewResult.model_validate_json(json_str)
        assert restored.compare_models == result.compare_models
        assert len(restored.model_results) == 2

    def test_json_has_merged_field_agreement(self):
        result = _compare_result(include_field_agreement=True)
        parsed = json.loads(to_compare_json(result))
        assert "merged" in parsed
        assert "field_agreement" in parsed["merged"]
        assert len(parsed["merged"]["field_agreement"]) == 2

    def test_json_has_model_results(self):
        result = _compare_result()
        parsed = json.loads(to_compare_json(result))
        assert "model_results" in parsed
        assert len(parsed["model_results"]) == 2

    def test_json_failed_model_has_error(self):
        result = _compare_result(model_b_failed=True)
        parsed = json.loads(to_compare_json(result))
        runs = {r["model_name"]: r for r in parsed["model_results"]}
        assert runs["model-B"]["error"] == "Failed for testing"


# ─── to_compare_charting_markdown ─────────────────────────────────────────────

class TestToCompareChartingMarkdown:
    def test_produces_non_empty_string(self):
        result = _compare_result(include_field_agreement=True)
        md = to_compare_charting_markdown(result)
        assert isinstance(md, str)
        assert len(md) > 50

    def test_agree_indicator_present(self):
        result = _compare_result(include_field_agreement=True)
        md = to_compare_charting_markdown(result)
        assert "✓ Agree" in md

    def test_differ_indicator_present(self):
        result = _compare_result(include_field_agreement=True)
        md = to_compare_charting_markdown(result)
        assert "⚠ Differ" in md

    def test_study_heading_present(self):
        result = _compare_result(include_field_agreement=True)
        md = to_compare_charting_markdown(result)
        assert "M-001" in md

    def test_fallback_when_no_field_agreement(self):
        result = _compare_result(include_field_agreement=False)
        md = to_compare_charting_markdown(result)
        assert "No field agreement" in md or "_No field" in md


# ─── to_compare_charting_json ─────────────────────────────────────────────────

class TestToCompareChartingJson:
    def test_produces_valid_json_array(self):
        result = _compare_result(include_field_agreement=True)
        json_str = to_compare_charting_json(result)
        parsed = json.loads(json_str)
        assert isinstance(parsed, list)

    def test_empty_array_when_no_field_agreement(self):
        result = _compare_result(include_field_agreement=False)
        json_str = to_compare_charting_json(result)
        parsed = json.loads(json_str)
        assert parsed == []

    def test_schema_structure(self):
        result = _compare_result(include_field_agreement=True)
        parsed = json.loads(to_compare_charting_json(result))
        assert len(parsed) >= 1
        study = parsed[0]
        assert "source_id" in study
        assert "charting" in study
        section = next(iter(study["charting"].values()))
        assert "fields" in section
        field = section["fields"][0]
        assert "field_name" in field
        assert "answer_type" in field
        assert "agreed" in field
        assert "values" in field

    def test_agreed_bool_correct(self):
        result = _compare_result(include_field_agreement=True)
        parsed = json.loads(to_compare_charting_json(result))
        study = parsed[0]
        all_fields = [f for sec in study["charting"].values() for f in sec["fields"]]
        agree_fields = [f for f in all_fields if f["field_name"] == "Feature Importance Reported"]
        differ_fields = [f for f in all_fields if f["field_name"] == "Top Features"]
        assert len(agree_fields) == 1
        assert agree_fields[0]["agreed"] is True
        assert len(differ_fields) == 1
        assert differ_fields[0]["agreed"] is False
