"""Contract tests for Feature 006: factories, JSON round-trips, and protocol extensions."""

import pytest
from pydantic import ValidationError

from prisma_review_agent.agents import default_charting_template, default_appraisal_config
from prisma_review_agent.models import (
    ChartingTemplate,
    CriticalAppraisalConfig,
    ReviewProtocol,
)


# ─── default_charting_template() ─────────────────────────────────────────────────────

class TestBridge2aiTemplate:
    def test_returns_charting_template(self):
        t = default_charting_template()
        assert isinstance(t, ChartingTemplate)

    def test_has_seven_sections(self):
        t = default_charting_template()
        keys = [s.section_key for s in t.sections]
        assert set(keys) == {"A", "B", "C", "D", "E", "F", "G"}

    def test_section_g_all_reviewer_only(self):
        t = default_charting_template()
        g = next(s for s in t.sections if s.section_key == "G")
        assert all(f.reviewer_only for f in g.fields)

    def test_total_field_count(self):
        t = default_charting_template()
        total = sum(len(s.fields) for s in t.sections)
        assert total == 60

    def test_llm_extractable_count(self):
        t = default_charting_template()
        extractable = sum(
            1 for s in t.sections for f in s.fields if not f.reviewer_only
        )
        assert extractable == 56

    def test_is_deterministic(self):
        assert default_charting_template() == default_charting_template()

    def test_json_round_trip(self):
        t = default_charting_template()
        reloaded = ChartingTemplate.model_validate_json(t.model_dump_json())
        assert reloaded == t

    def test_enumerated_fields_have_options(self):
        t = default_charting_template()
        for section in t.sections:
            for field in section.fields:
                if field.answer_type in ("enumerated", "yes_no_extended"):
                    assert field.options, (
                        f"Section {section.section_key} field '{field.field_name}' "
                        f"has answer_type={field.answer_type} but no options"
                    )


# ─── default_appraisal_config() ─────────────────────────────────────────────

class TestBridge2aiAppraisalConfig:
    def test_returns_config(self):
        c = default_appraisal_config()
        assert isinstance(c, CriticalAppraisalConfig)

    def test_has_four_domains(self):
        c = default_appraisal_config()
        assert len(c.domains) == 4

    def test_total_item_count(self):
        c = default_appraisal_config()
        total = sum(len(d.items) for d in c.domains)
        assert total == 17

    def test_is_deterministic(self):
        assert default_appraisal_config() == default_appraisal_config()

    def test_json_round_trip(self):
        c = default_appraisal_config()
        reloaded = CriticalAppraisalConfig.model_validate_json(c.model_dump_json())
        assert reloaded == c

    def test_all_aggregation_rules_valid(self):
        valid = {"majority_yes", "strict", "lenient"}
        c = default_appraisal_config()
        for domain in c.domains:
            assert domain.concern_aggregation_rule in valid


# ─── ReviewProtocol extensions ────────────────────────────────────────────────

class TestReviewProtocolExtensions:
    def test_accepts_charting_template(self):
        protocol = ReviewProtocol(charting_template=default_charting_template())
        assert protocol.charting_template is not None

    def test_accepts_appraisal_config(self):
        protocol = ReviewProtocol(critical_appraisal_config=default_appraisal_config())
        assert protocol.critical_appraisal_config is not None

    def test_defaults_to_none(self):
        protocol = ReviewProtocol()
        assert protocol.charting_template is None
        assert protocol.critical_appraisal_config is None

    def test_wrong_type_for_charting_template_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ReviewProtocol(charting_template="not_a_template")

    def test_wrong_type_for_appraisal_config_raises(self):
        with pytest.raises((ValueError, ValidationError)):
            ReviewProtocol(critical_appraisal_config="not_a_config")
