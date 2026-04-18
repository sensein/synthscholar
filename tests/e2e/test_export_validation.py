"""US3: Export format validation using pre-built PRISMAReviewResult fixture."""

from __future__ import annotations

import json

import pytest

from prisma_review_agent.models import PRISMAReviewResult
from prisma_review_agent.export import (
    to_markdown,
    to_json,
    to_bibtex,
    to_turtle,
    to_jsonld,
    to_narrative_summary_markdown,
    to_narrative_summary_json,
    to_rubric_markdown,
    to_rubric_json,
    to_charting_markdown,
    to_charting_json,
    to_appraisal_markdown,
    to_appraisal_json,
)


# ── Core exports ──────────────────────────────────────────────────────────────

def test_to_markdown_non_empty(result_fixture):
    md = to_markdown(result_fixture)
    assert len(md) > 0


def test_to_json_valid(result_fixture):
    parsed = json.loads(to_json(result_fixture))
    assert isinstance(parsed, dict)


def test_to_bibtex_has_reference(result_fixture):
    bib = to_bibtex(result_fixture)
    if result_fixture.included_articles:
        assert "@" in bib
    else:
        assert isinstance(bib, str)


def test_to_turtle_parseable(result_fixture):
    import rdflib
    ttl = to_turtle(result_fixture)
    g = rdflib.Graph()
    g.parse(data=ttl, format="turtle")


def test_to_jsonld_parseable(result_fixture):
    import rdflib
    jld = to_jsonld(result_fixture)
    g = rdflib.Graph()
    g.parse(data=jld, format="json-ld")


# ── Narrative summary exports ─────────────────────────────────────────────────

def test_to_narrative_summary_markdown_has_rows(result_fixture):
    md = to_narrative_summary_markdown(result_fixture)
    assert len(md) > 0
    if result_fixture.narrative_rows:
        assert "|" in md


def test_to_narrative_summary_json_valid(result_fixture):
    parsed = json.loads(to_narrative_summary_json(result_fixture))
    assert isinstance(parsed, (dict, list))


# ── Rubric exports ────────────────────────────────────────────────────────────

def test_to_rubric_markdown_non_empty(result_fixture):
    md = to_rubric_markdown(result_fixture)
    assert len(md) > 0


def test_to_rubric_json_valid(result_fixture):
    parsed = json.loads(to_rubric_json(result_fixture))
    assert isinstance(parsed, (dict, list))


# ── Charting exports ──────────────────────────────────────────────────────────

def test_to_charting_markdown_has_sections(result_fixture):
    md = to_charting_markdown(result_fixture)
    assert len(md) > 0
    if result_fixture.included_articles:
        assert "Section" in md or "Publication" in md


def test_to_charting_json_valid(result_fixture):
    parsed = json.loads(to_charting_json(result_fixture))
    assert isinstance(parsed, (dict, list))


# ── Appraisal exports ─────────────────────────────────────────────────────────

def test_to_appraisal_markdown_has_table(result_fixture):
    md = to_appraisal_markdown(result_fixture)
    assert len(md) > 0
    if result_fixture.critical_appraisals:
        assert "|" in md


def test_to_appraisal_json_has_studies_key(result_fixture):
    parsed = json.loads(to_appraisal_json(result_fixture))
    assert "studies" in parsed


# ── Minimal-result robustness test ────────────────────────────────────────────

def test_all_exports_with_minimal_result():
    minimal = PRISMAReviewResult(research_question="Minimal test review")
    md = to_markdown(minimal)
    assert len(md) > 0
    js = to_json(minimal)
    assert json.loads(js) is not None
    bib = to_bibtex(minimal)
    assert isinstance(bib, str)
