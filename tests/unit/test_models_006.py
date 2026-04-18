"""Unit tests for Feature 006 Pydantic models (field-level appraisal output schema)."""

import pytest
from pydantic import ValidationError

from prisma_review_agent.models import (
    FieldDefinition,
    ChartingSection,
    ChartingTemplate,
    FieldAnswer,
    SectionExtractionResult,
    AppraisalItemSpec,
    AppraisalDomainSpec,
    CriticalAppraisalConfig,
    ItemRating,
    DomainAppraisal,
    CriticalAppraisalResult,
)


# ─── FieldDefinition validation ───────────────────────────────────────────────

class TestFieldDefinition:
    def test_free_text_no_options(self):
        fd = FieldDefinition(field_name="Title", description="Full title", answer_type="free_text")
        assert fd.options is None
        assert fd.reviewer_only is False

    def test_enumerated_requires_options(self):
        with pytest.raises((ValueError, ValidationError)):
            FieldDefinition(field_name="X", description="X", answer_type="enumerated", options=[])

    def test_enumerated_with_options_ok(self):
        fd = FieldDefinition(
            field_name="Study Design",
            description="Design",
            answer_type="enumerated",
            options=["Cross-sectional", "Longitudinal"],
        )
        assert fd.options == ["Cross-sectional", "Longitudinal"]

    def test_yes_no_extended_valid_not_reported(self):
        fd = FieldDefinition(
            field_name="Comorbidities",
            description="Comorbidities",
            answer_type="yes_no_extended",
            options=["Yes", "No", "Not Reported"],
        )
        assert fd.options == ["Yes", "No", "Not Reported"]

    def test_yes_no_extended_valid_na(self):
        fd = FieldDefinition(
            field_name="Matched",
            description="Matched",
            answer_type="yes_no_extended",
            options=["Yes", "No", "N/A"],
        )
        assert fd.options == ["Yes", "No", "N/A"]

    def test_yes_no_extended_invalid_options(self):
        with pytest.raises((ValueError, ValidationError)):
            FieldDefinition(
                field_name="X",
                description="X",
                answer_type="yes_no_extended",
                options=["Yes", "No", "Maybe"],
            )

    def test_yes_no_extended_empty_options(self):
        with pytest.raises((ValueError, ValidationError)):
            FieldDefinition(
                field_name="X",
                description="X",
                answer_type="yes_no_extended",
                options=[],
            )

    def test_reviewer_only_flag(self):
        fd = FieldDefinition(
            field_name="Summary",
            description="Key findings",
            answer_type="free_text",
            reviewer_only=True,
        )
        assert fd.reviewer_only is True

    def test_numeric_no_options(self):
        fd = FieldDefinition(field_name="N", description="Count", answer_type="numeric")
        assert fd.options is None


# ─── ChartingTemplate ─────────────────────────────────────────────────────────

class TestChartingTemplate:
    def _make_section(self, key: str) -> ChartingSection:
        return ChartingSection(
            section_key=key,
            section_title=f"Section {key}",
            fields=[
                FieldDefinition(field_name="F1", description="desc", answer_type="free_text")
            ],
        )

    def test_add_section_duplicate_key_raises(self):
        template = ChartingTemplate(sections=[self._make_section("A")])
        with pytest.raises(ValueError):
            template.add_section("A", "Duplicate", [])

    def test_add_section_returns_new_instance(self):
        template = ChartingTemplate(sections=[self._make_section("A")])
        new_field = FieldDefinition(field_name="F2", description="d", answer_type="free_text")
        new_template = template.add_section("B", "Section B", [new_field])
        assert len(template.sections) == 1
        assert len(new_template.sections) == 2

    def test_override_field_returns_new_instance(self):
        template = ChartingTemplate(sections=[self._make_section("A")])
        new_template = template.override_field("A", "F1", description="updated")
        assert template.sections[0].fields[0].description == "desc"
        assert new_template.sections[0].fields[0].description == "updated"

    def test_json_round_trip(self):
        template = ChartingTemplate(sections=[self._make_section("A")])
        reloaded = ChartingTemplate.model_validate_json(template.model_dump_json())
        assert reloaded == template


# ─── CriticalAppraisalConfig validation ───────────────────────────────────────

class TestCriticalAppraisalConfig:
    def _make_item(self) -> AppraisalItemSpec:
        return AppraisalItemSpec(
            item_text="Was sample size adequate?",
            allowed_ratings=["Yes", "Partial", "No", "Not Reported"],
        )

    def test_empty_items_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            AppraisalDomainSpec(
                domain_name="Sample Quality",
                items=[],
                concern_aggregation_rule="majority_yes",
            )

    def test_invalid_aggregation_rule_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            AppraisalDomainSpec(
                domain_name="Quality",
                items=[self._make_item()],
                concern_aggregation_rule="invalid_rule",
            )

    def test_valid_config(self):
        config = CriticalAppraisalConfig(
            domains=[
                AppraisalDomainSpec(
                    domain_name="Sample Quality",
                    items=[self._make_item()],
                    concern_aggregation_rule="majority_yes",
                )
            ]
        )
        assert len(config.domains) == 1

    def test_json_round_trip(self):
        config = CriticalAppraisalConfig(
            domains=[
                AppraisalDomainSpec(
                    domain_name="Sample Quality",
                    items=[self._make_item()],
                    concern_aggregation_rule="strict",
                )
            ]
        )
        reloaded = CriticalAppraisalConfig.model_validate_json(config.model_dump_json())
        assert reloaded == config


# ─── DomainAppraisal concern constraint ───────────────────────────────────────

class TestDomainAppraisal:
    def test_valid_concerns(self):
        for concern in ("Low", "Some", "High"):
            da = DomainAppraisal(
                domain_name="D",
                item_ratings=[ItemRating(item_text="Q?", rating="Yes")],
                domain_concern=concern,
            )
            assert da.domain_concern == concern

    def test_invalid_concern_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            DomainAppraisal(
                domain_name="D",
                item_ratings=[ItemRating(item_text="Q?", rating="Yes")],
                domain_concern="Medium",
            )
