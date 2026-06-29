"""Unit tests for the search-synthesis article block helper.

Both ``run_search_synthesis`` and ``run_per_disorder_synthesis`` feed each
article through ``_summarise_article_for_search`` to build the prompt block.
The block must opportunistically include the full-text excerpt and
pre-extracted findings when those are populated, and degrade gracefully to
title + abstract only when they aren't.
"""

from __future__ import annotations

from synthscholar.agents import _SEARCH_FULL_TEXT_CHARS, _summarise_article_for_search
from synthscholar.models import Article, StudyDataExtraction


def test_abstract_only_block_omits_full_text_section():
    art = Article(pmid="1", title="T", year="2024", abstract="The abstract.")
    block = _summarise_article_for_search(art, 1)
    assert "Abstract: The abstract." in block
    assert "Full-text excerpt:" not in block
    assert "Pre-extracted findings:" not in block


def test_full_text_included_when_present():
    body = "Methods: cohort of 137 adults. Results: AUC 0.86 (95% CI 0.79-0.91)."
    art = Article(pmid="2", title="T", abstract="A", full_text=body)
    block = _summarise_article_for_search(art, 1)
    assert "Full-text excerpt:" in block
    assert "AUC 0.86" in block
    assert "Methods:" in block


def test_full_text_truncated_to_budget():
    big = "x" * (_SEARCH_FULL_TEXT_CHARS + 5_000)
    art = Article(pmid="3", title="T", abstract="A", full_text=big)
    block = _summarise_article_for_search(art, 1)
    rendered_ft = block.split("Full-text excerpt: ", 1)[1]
    # Full-text excerpt is the last section in this fixture (no findings).
    assert len(rendered_ft) == _SEARCH_FULL_TEXT_CHARS


def test_pre_extracted_findings_appended_when_present():
    art = Article(
        pmid="4", title="T", abstract="A",
        extracted_data=StudyDataExtraction(
            key_findings=[
                "Sensitivity 0.79",
                "Specificity 0.81",
                "n=512 included",
            ],
        ),
    )
    block = _summarise_article_for_search(art, 1)
    assert "Pre-extracted findings: Sensitivity 0.79; Specificity 0.81; n=512 included" in block


def test_findings_capped_at_first_five():
    art = Article(
        pmid="5", title="T", abstract="A",
        extracted_data=StudyDataExtraction(
            key_findings=[f"finding {i}" for i in range(8)],
        ),
    )
    block = _summarise_article_for_search(art, 1)
    assert "finding 0" in block
    assert "finding 4" in block
    assert "finding 5" not in block


def test_empty_extracted_findings_is_skipped():
    art = Article(
        pmid="6", title="T", abstract="A",
        extracted_data=StudyDataExtraction(key_findings=[]),
    )
    block = _summarise_article_for_search(art, 1)
    assert "Pre-extracted findings:" not in block


def test_field_order_is_stable():
    art = Article(
        pmid="7", title="T", year="2024", abstract="A", full_text="FT",
        extracted_data=StudyDataExtraction(key_findings=["F1"]),
    )
    block = _summarise_article_for_search(art, 1)
    title_idx = block.index("Title:")
    year_idx = block.index("Year:")
    abstract_idx = block.index("Abstract:")
    ft_idx = block.index("Full-text excerpt:")
    findings_idx = block.index("Pre-extracted findings:")
    assert title_idx < year_idx < abstract_idx < ft_idx < findings_idx
