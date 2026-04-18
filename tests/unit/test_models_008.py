"""Unit tests for Feature 008 Bug 1: Article.inclusion_status enum coercion."""

import warnings

import pytest

from prisma_review_agent.models import Article, InclusionStatus


def _make_article(**overrides) -> Article:
    base: dict = dict(
        pmid="38821669",
        inclusion_status=InclusionStatus.PENDING,
    )
    base.update(overrides)
    return Article(**base)


class TestInclusionStatusEnumMembers:
    def test_enum_values_are_correct(self):
        assert InclusionStatus.INCLUDED.value == "included"
        assert InclusionStatus.EXCLUDED.value == "excluded"
        assert InclusionStatus.PENDING.value == "pending"

    def test_included_member_identity(self):
        art = _make_article(inclusion_status=InclusionStatus.INCLUDED)
        assert art.inclusion_status is InclusionStatus.INCLUDED

    def test_excluded_member_identity(self):
        art = _make_article(inclusion_status=InclusionStatus.EXCLUDED)
        assert art.inclusion_status is InclusionStatus.EXCLUDED


class TestInclusionStatusSerialization:
    def test_included_enum_serializes_without_pydantic_warning(self):
        art = _make_article(inclusion_status=InclusionStatus.INCLUDED)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            art.model_dump_json()
        bad = [x for x in w if "inclusion_status" in str(x.message)]
        assert bad == [], f"Unexpected warnings: {bad}"

    def test_excluded_enum_serializes_without_pydantic_warning(self):
        art = _make_article(inclusion_status=InclusionStatus.EXCLUDED)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            art.model_dump_json()
        bad = [x for x in w if "inclusion_status" in str(x.message)]
        assert bad == [], f"Unexpected warnings: {bad}"

    def test_included_serializes_to_string_included(self):
        art = _make_article(inclusion_status=InclusionStatus.INCLUDED)
        assert art.model_dump()["inclusion_status"] == "included"

    def test_excluded_serializes_to_string_excluded(self):
        art = _make_article(inclusion_status=InclusionStatus.EXCLUDED)
        assert art.model_dump()["inclusion_status"] == "excluded"

    def test_plain_string_causes_pydantic_warning(self):
        """Regression guard: documents that plain string triggers the old bug."""
        art = _make_article(inclusion_status=InclusionStatus.INCLUDED)
        art.inclusion_status = "included"  # type: ignore[assignment]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            art.model_dump_json()
        assert any("inclusion_status" in str(x.message) for x in w), (
            "Expected PydanticSerializationUnexpectedValue for plain string"
        )

    def test_batch_of_articles_no_warnings(self):
        articles = [
            _make_article(pmid=str(i), inclusion_status=InclusionStatus.INCLUDED)
            for i in range(11)
        ]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            for art in articles:
                art.model_dump_json()
        bad = [x for x in w if "inclusion_status" in str(x.message)]
        assert bad == [], f"Got {len(bad)} warnings for 11-article batch"
