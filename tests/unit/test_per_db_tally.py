"""Unit tests for the per-database PRISMA tally helper."""

from __future__ import annotations

from synthscholar.models import Article, PRISMAFlowCounts
from synthscholar.pipeline import _apply_per_db_tally


def _art(pmid: str, source: str) -> Article:
    return Article(pmid=pmid, source=source)


def test_empty_dict_zeros_all_counts():
    flow = PRISMAFlowCounts()
    _apply_per_db_tally(flow, {})
    assert flow.db_pubmed == 0
    assert flow.db_biorxiv == 0
    assert flow.db_medrxiv == 0
    assert flow.db_related == 0
    assert flow.db_hops == 0
    assert flow.db_other_sources == {}


def test_known_sources_counted_separately():
    arts = {
        "p1": _art("p1", "pubmed_search"),
        "p2": _art("p2", "pubmed_search"),
        "b1": _art("b1", "biorxiv"),
        "m1": _art("m1", "medrxiv"),
        "m2": _art("m2", "medrxiv"),
        "r1": _art("r1", "related_1"),
        "r2": _art("r2", "related_2"),
        "h1": _art("h1", "hop_1"),
    }
    flow = PRISMAFlowCounts()
    _apply_per_db_tally(flow, arts)
    assert flow.db_pubmed == 2
    assert flow.db_biorxiv == 1
    assert flow.db_medrxiv == 2
    assert flow.db_related == 2
    assert flow.db_hops == 1
    assert flow.db_other_sources == {}


def test_medrxiv_no_longer_silently_dropped():
    """Regression: medRxiv used to be uncounted because the old code only
    summed sources equal to ``"biorxiv"``. New tally must surface it."""
    arts = {f"m{i}": _art(f"m{i}", "medrxiv") for i in range(5)}
    flow = PRISMAFlowCounts()
    _apply_per_db_tally(flow, arts)
    assert flow.db_medrxiv == 5
    assert flow.db_biorxiv == 0


def test_unknown_sources_land_in_db_other_sources():
    arts = {
        "o1": _art("o1", "openalex"),
        "o2": _art("o2", "openalex"),
        "e1": _art("e1", "europepmc"),
        "c1": _art("c1", "crossref"),
    }
    flow = PRISMAFlowCounts()
    _apply_per_db_tally(flow, arts)
    assert flow.db_other_sources == {"openalex": 2, "europepmc": 1, "crossref": 1}
    assert flow.db_pubmed == flow.db_biorxiv == flow.db_medrxiv == 0


def test_empty_source_string_skipped():
    flow = PRISMAFlowCounts()
    _apply_per_db_tally(flow, {"x1": _art("x1", "")})
    assert flow.db_other_sources == {}


def test_idempotent_recompute_replaces_previous_counts():
    flow = PRISMAFlowCounts(
        db_pubmed=99, db_biorxiv=99, db_medrxiv=99, db_related=99, db_hops=99,
        db_other_sources={"stale": 99},
    )
    _apply_per_db_tally(flow, {"p1": _art("p1", "pubmed_search")})
    assert flow.db_pubmed == 1
    assert flow.db_biorxiv == 0
    assert flow.db_medrxiv == 0
    assert flow.db_related == 0
    assert flow.db_hops == 0
    assert flow.db_other_sources == {}


def test_related_and_hop_match_by_prefix():
    arts = {
        "r1": _art("r1", "related_1"),
        "r5": _art("r5", "related_5"),
        "h2": _art("h2", "hop_2"),
        "h99": _art("h99", "hop_99"),
    }
    flow = PRISMAFlowCounts()
    _apply_per_db_tally(flow, arts)
    assert flow.db_related == 2
    assert flow.db_hops == 2


def test_total_identified_equals_sum_of_buckets():
    arts = {
        "p1": _art("p1", "pubmed_search"),
        "b1": _art("b1", "biorxiv"),
        "m1": _art("m1", "medrxiv"),
        "r1": _art("r1", "related_1"),
        "h1": _art("h1", "hop_1"),
        "o1": _art("o1", "openalex"),
        "o2": _art("o2", "europepmc"),
    }
    flow = PRISMAFlowCounts()
    _apply_per_db_tally(flow, arts)
    bucketed = (flow.db_pubmed + flow.db_biorxiv + flow.db_medrxiv
                + flow.db_related + flow.db_hops
                + sum(flow.db_other_sources.values()))
    assert bucketed == len(arts)
