"""Unit tests for deterministic per-disorder synthesis bucketing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from synthscholar import agents as agents_mod
from synthscholar.agents import (
    AgentDeps,
    disorder_labels_from_rubrics,
    run_per_disorder_synthesis,
)
from synthscholar.models import (
    Article,
    DataChartingRubric,
    GroupSummary,
    PerDisorderSynthesis,
    ReviewProtocol,
)


def _art(pmid: str, title: str = "T") -> Article:
    return Article(pmid=pmid, title=title, abstract="A")


def _deps() -> AgentDeps:
    return AgentDeps(
        protocol=ReviewProtocol(question="test"),
        api_key="test",
        model_name="test",
        model=object(),
    )


@pytest.fixture
def stub_traced(monkeypatch):
    """Replace ``run_traced`` with a deterministic stub that echoes the bucket label."""
    calls: list[dict] = []

    async def _stub(agent, prompt, *, deps, model, step_name, iteration_mode="zero_shot", **_):
        first_line = prompt.splitlines()[0]
        label = first_line.removeprefix("Disorder label: ").strip()
        calls.append({"label": label, "step_name": step_name, "iteration_mode": iteration_mode})
        gs = GroupSummary(
            label=label,
            n_studies=999,
            aggregate_finding=f"Stubbed finding for {label}.",
            representative_pmids=[],
            caveats="",
        )
        return SimpleNamespace(output=gs)

    monkeypatch.setattr(agents_mod, "run_traced", _stub)
    return calls


# ─── disorder_labels_from_rubrics ────────────────────────────────────────────

class TestDisorderLabelsFromRubrics:
    def test_basic_mapping(self):
        rubrics = [
            DataChartingRubric(source_id="111", disorder_cohort="Parkinson's disease"),
            DataChartingRubric(source_id="222", disorder_cohort="Alzheimer's disease"),
        ]
        assert disorder_labels_from_rubrics(rubrics) == {
            "111": "Parkinson's disease",
            "222": "Alzheimer's disease",
        }

    def test_empty_cohort_skipped(self):
        rubrics = [
            DataChartingRubric(source_id="111", disorder_cohort=""),
            DataChartingRubric(source_id="222", disorder_cohort="   "),
            DataChartingRubric(source_id="333", disorder_cohort="ALS"),
        ]
        assert disorder_labels_from_rubrics(rubrics) == {"333": "ALS"}

    def test_missing_source_id_skipped(self):
        rubrics = [DataChartingRubric(source_id="", disorder_cohort="ALS")]
        assert disorder_labels_from_rubrics(rubrics) == {}


# ─── run_per_disorder_synthesis ──────────────────────────────────────────────

class TestRunPerDisorderSynthesis:
    @pytest.mark.asyncio
    async def test_empty_articles_short_circuits(self):
        out = await run_per_disorder_synthesis([], {}, _deps())
        assert isinstance(out, PerDisorderSynthesis)
        assert out.n_articles_synthesized == 0
        assert out.n_disorders == 0
        assert out.unlabeled_count == 0
        assert out.groups == []

    @pytest.mark.asyncio
    async def test_buckets_by_label_and_counts(self, stub_traced):
        arts = [_art("1"), _art("2"), _art("3"), _art("4")]
        labels = {"1": "Parkinson's", "2": "Parkinson's", "3": "ALS", "4": "ALS"}
        out = await run_per_disorder_synthesis(arts, labels, _deps(), topic="motor disorders")

        assert out.topic == "motor disorders"
        assert out.n_articles_synthesized == 4
        assert out.unlabeled_count == 0
        assert out.n_disorders == 2
        # n_studies must reflect the deterministic bucket size, not the LLM stub's value.
        by_label = {g.label: g for g in out.groups}
        assert set(by_label) == {"Parkinson's", "ALS"}
        assert by_label["Parkinson's"].n_studies == 2
        assert by_label["ALS"].n_studies == 2

    @pytest.mark.asyncio
    async def test_normalisation_groups_case_and_whitespace_variants(self, stub_traced):
        arts = [_art("1"), _art("2"), _art("3")]
        labels = {
            "1": "Parkinson's disease",
            "2": "parkinson's  disease",
            "3": "PARKINSON'S DISEASE ",
        }
        out = await run_per_disorder_synthesis(arts, labels, _deps())
        assert out.n_disorders == 1
        # First-seen casing is preserved as the display label.
        assert out.groups[0].label == "Parkinson's disease"
        assert out.groups[0].n_studies == 3

    @pytest.mark.asyncio
    async def test_unlabeled_articles_excluded_and_counted(self, stub_traced):
        arts = [_art("1"), _art("2"), _art("3"), _art("4")]
        labels = {"1": "ALS"}  # 2/3/4 unlabeled
        out = await run_per_disorder_synthesis(arts, labels, _deps())
        assert out.n_articles_synthesized == 4
        assert out.unlabeled_count == 3
        assert out.n_disorders == 1
        assert out.groups[0].label == "ALS"
        assert out.groups[0].n_studies == 1

    @pytest.mark.asyncio
    async def test_min_articles_filter_skips_small_buckets(self, stub_traced):
        arts = [_art("1"), _art("2"), _art("3")]
        labels = {"1": "ALS", "2": "ALS", "3": "Rare"}
        out = await run_per_disorder_synthesis(
            arts, labels, _deps(), min_articles_per_disorder=2,
        )
        # "Rare" had only 1 article — skipped from groups but still counted.
        assert out.n_articles_synthesized == 3
        assert out.n_disorders == 1
        assert out.groups[0].label == "ALS"

    @pytest.mark.asyncio
    async def test_groups_sorted_by_size_then_label(self, stub_traced):
        arts = [_art(str(i)) for i in range(1, 7)]
        labels = {
            "1": "ALS", "2": "ALS",
            "3": "Parkinson's", "4": "Parkinson's", "5": "Parkinson's",
            "6": "Alzheimer's",
        }
        out = await run_per_disorder_synthesis(arts, labels, _deps())
        assert [g.label for g in out.groups] == ["Parkinson's", "ALS", "Alzheimer's"]
