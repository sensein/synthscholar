"""Integration tests for Feature 006 export functions."""

import json
import pytest

from prisma_review_agent.models import (
    PRISMAReviewResult,
    PrismaReview,
    Methods,
    PrismaFlow,
    Abstract,
    Introduction,
    Results,
    Discussion,
    Conclusion,
    OutputFormat,
    BiasAssessment,
    Implications,
    StudyDataExtractionReport,
    SectionExtractionResult,
    FieldAnswer,
    CriticalAppraisalResult,
    DomainAppraisal,
    ItemRating,
)
from prisma_review_agent.export import (
    to_charting_markdown,
    to_charting_json,
    to_appraisal_markdown,
    to_appraisal_json,
)


def _make_result(
    with_field_answers: bool = True,
    with_appraisal: bool = True,
) -> PRISMAReviewResult:
    field_answers: dict = {}
    if with_field_answers:
        field_answers = {
            "A": SectionExtractionResult(
                section_key="A",
                section_title="Publication Information",
                field_answers=[
                    FieldAnswer(field_name="Source ID", value="M-001", confidence="high"),
                    FieldAnswer(field_name="Year", value="2023", confidence="high"),
                ],
            )
        }

    appraisal_results: list = []
    if with_appraisal:
        appraisal_results = [
            CriticalAppraisalResult(
                source_id="M-001",
                domains=[
                    DomainAppraisal(
                        domain_name="Sample Quality",
                        item_ratings=[
                            ItemRating(item_text="Was sample size adequate?", rating="Yes"),
                            ItemRating(item_text="Were demographics reported?", rating="Partial"),
                        ],
                        domain_concern="Some",
                    )
                ],
            )
        ]

    methods = Methods(
        search_strategy="PubMed + bioRxiv",
        study_selection=PrismaFlow(final_included=1),
        inclusion_criteria=["Bio-acoustic"],
        exclusion_criteria=["Non-English"],
        data_extraction_schema=[],
        data_extraction=[
            StudyDataExtractionReport(
                source_id="M-001",
                sections={},
                field_answers=field_answers,
            )
        ],
        quality_assessment="Custom appraisal tool",
        critical_appraisal_results=appraisal_results,
    )

    prisma_review = PrismaReview(
        title="Test Review",
        abstract=Abstract(background="b", objective="o", methods="m", results="r", conclusion="c"),
        introduction=Introduction(
            background="b", problem_statement="p", research_gap="g", objectives="o"
        ),
        methods=methods,
        results=Results(
            output_format=OutputFormat(style="paragraph"),
            prisma_flow_summary=PrismaFlow(final_included=1),
            themes=[],
            bias_assessment=BiasAssessment(
                overall_quality="Moderate", common_biases=[], risk_level="moderate"
            ),
        ),
        discussion=Discussion(
            summary_of_findings="s",
            interpretation="i",
            comparison_with_literature="c",
            implications=Implications(clinical="c", policy="p", research="r"),
            limitations="l",
        ),
        conclusion=Conclusion(key_takeaways="k", recommendations="r", future_research="f"),
        references=[],
    )

    return PRISMAReviewResult(
        research_question="Test",
        prisma_review=prisma_review,
    )


# ─── to_charting_markdown ─────────────────────────────────────────────────────

class TestToChartingMarkdown:
    def test_contains_source_id(self):
        result = _make_result()
        md = to_charting_markdown(result)
        assert "M-001" in md

    def test_contains_section_title(self):
        result = _make_result()
        md = to_charting_markdown(result)
        assert "Publication Information" in md

    def test_contains_field_name_and_value(self):
        result = _make_result()
        md = to_charting_markdown(result)
        assert "Source ID" in md
        assert "M-001" in md

    def test_fallback_when_no_field_answers(self):
        result = _make_result(with_field_answers=False)
        md = to_charting_markdown(result)
        assert "not available" in md.lower() or "no" in md.lower()

    def test_fallback_when_no_prisma_review(self):
        result = PRISMAReviewResult(research_question="Test")
        md = to_charting_markdown(result)
        assert "not available" in md.lower() or "no" in md.lower()


# ─── to_charting_json ─────────────────────────────────────────────────────────

class TestToChartingJson:
    def test_returns_valid_json(self):
        result = _make_result()
        data = json.loads(to_charting_json(result))
        assert isinstance(data, list)

    def test_source_id_in_output(self):
        result = _make_result()
        data = json.loads(to_charting_json(result))
        assert data[0]["source_id"] == "M-001"

    def test_charting_key_present(self):
        result = _make_result()
        data = json.loads(to_charting_json(result))
        assert "charting" in data[0]
        assert "A" in data[0]["charting"]

    def test_field_structure(self):
        result = _make_result()
        data = json.loads(to_charting_json(result))
        fields = data[0]["charting"]["A"]["fields"]
        assert any(f["field_name"] == "Source ID" for f in fields)


# ─── to_appraisal_markdown ────────────────────────────────────────────────────

class TestToAppraisalMarkdown:
    def test_contains_source_id(self):
        result = _make_result()
        md = to_appraisal_markdown(result)
        assert "M-001" in md

    def test_contains_domain_name(self):
        result = _make_result()
        md = to_appraisal_markdown(result)
        assert "Sample Quality" in md

    def test_contains_domain_concern(self):
        result = _make_result()
        md = to_appraisal_markdown(result)
        assert "Some" in md

    def test_cross_study_summary_present(self):
        result = _make_result()
        md = to_appraisal_markdown(result)
        assert "Cross-Study" in md or "Summary" in md

    def test_fallback_when_no_appraisal(self):
        result = _make_result(with_appraisal=False)
        md = to_appraisal_markdown(result)
        assert "not available" in md.lower() or "no" in md.lower()


# ─── to_appraisal_json ────────────────────────────────────────────────────────

class TestToAppraisalJson:
    def test_returns_valid_json(self):
        result = _make_result()
        data = json.loads(to_appraisal_json(result))
        assert isinstance(data, dict)

    def test_studies_key_present(self):
        result = _make_result()
        data = json.loads(to_appraisal_json(result))
        assert "studies" in data

    def test_summary_key_present(self):
        result = _make_result()
        data = json.loads(to_appraisal_json(result))
        assert "summary" in data

    def test_domain_in_output(self):
        result = _make_result()
        data = json.loads(to_appraisal_json(result))
        appraisal = data["studies"][0]["appraisal"]
        assert appraisal[0]["domain_name"] == "Sample Quality"

    def test_summary_aggregation(self):
        result = _make_result()
        data = json.loads(to_appraisal_json(result))
        summary = data["summary"]
        assert "Sample Quality" in summary
        assert summary["Sample Quality"]["Some"] == 1
