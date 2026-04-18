"""
Export functions for PRISMA review results.

Supports Markdown (PRISMA 2020 format), JSON, BibTeX, Turtle, JSON-LD,
and a queryable pyoxigraph RDF store.
"""

from __future__ import annotations

import re
import json

from .models import PRISMAReviewResult, CompareReviewResult
from .ontology.rdf_export import to_turtle, to_jsonld  # noqa: F401 — re-exported
from .ontology.rdf_store import SLRStore  # noqa: F401 — re-exported

__all__ = [
    "to_markdown", "to_bibtex", "to_json", "to_turtle", "to_jsonld",
    "to_oxigraph_store", "to_rubric_markdown", "to_rubric_json",
    # Feature 006
    "to_charting_markdown", "to_charting_json",
    "to_appraisal_markdown", "to_appraisal_json",
    # Feature 007
    "to_compare_markdown", "to_compare_json",
    "to_compare_charting_markdown", "to_compare_charting_json",
]


def to_oxigraph_store(result: PRISMAReviewResult) -> SLRStore:
    """Load *result* into an in-memory pyoxigraph store and return it.

    The returned store is immediately queryable via SPARQL::

        store = to_oxigraph_store(result)
        rows = store.query(
            "SELECT ?src WHERE { ?src a <https://w3id.org/slr-ontology/IncludedSource> }"
        )
    """
    store = SLRStore()
    store.load(result)
    return store


def to_markdown(result: PRISMAReviewResult) -> str:
    """Export review as PRISMA 2020 structured Markdown."""
    p = result.protocol
    f = result.flow
    included = result.included_articles

    cache_banner = ""
    if result.cache_hit:
        matched_title = result.cache_matched_criteria.get("title", "")
        score_pct = f"{result.cache_similarity_score:.1%}"
        cache_banner = (
            f"\n> **⚡ Served from cache** (similarity {score_pct})"
            + (f" — matched: *{matched_title}*" if matched_title else "")
            + "\n"
        )

    lines = [
        f"# {p.title or 'Systematic Review'}",
        f"\n*Generated: {result.timestamp} | PRISMA 2020 Compliant*\n",
        cache_banner,
        "---\n",

        "## Abstract\n",
        f"**Objective:** {p.objective}\n",
        f"**Methods:** A systematic search of {', '.join(p.databases)} was conducted. "
        f"Studies were screened against predefined criteria. Risk of bias was assessed "
        f"using {p.rob_tool.value}.\n",
        f"**Results:** {f.total_identified} records were identified. After removing "
        f"{f.duplicates_removed} duplicates and screening, {f.included_synthesis} "
        f"studies were included.\n",

        "---\n",
        "## 1. Introduction\n",
        f"### 1.1 Rationale (PRISMA Item 3)\n\n{p.objective}\n",
        "### 1.2 Objectives (PRISMA Item 4)\n",
        f"- **Population:** {p.pico_population or 'N/A'}",
        f"- **Intervention:** {p.pico_intervention or 'N/A'}",
        f"- **Comparison:** {p.pico_comparison or 'N/A'}",
        f"- **Outcome:** {p.pico_outcome or 'N/A'}\n",

        "---\n",
        "## 2. Methods\n",
        "### 2.1 Eligibility Criteria (PRISMA Item 5)\n",
        f"**Inclusion:** {p.inclusion_criteria}\n",
        f"**Exclusion:** {p.exclusion_criteria}\n",
        f"### 2.2 Information Sources (PRISMA Item 6)\n",
        f"Databases searched: {', '.join(p.databases)}\n",
        f"Date of last search: {result.timestamp[:10]}\n",
        f"Multi-hop citation navigation: up to {p.max_hops} hops\n",
        "### 2.3 Search Strategy (PRISMA Item 7)\n",
        "Queries used:",
    ]
    for q in result.search_queries:
        lines.append(f"- `{q}`")

    lines.extend([
        "\n### 2.4 Selection Process (PRISMA Item 8)\n",
        "Studies were screened using AI-assisted batch screening with human "
        "verification. Title/abstract screening was inclusive; full-text "
        "screening was strict.\n",
        f"### 2.5 Risk of Bias (PRISMA Item 11)\n",
        f"Risk of bias was assessed using **{p.rob_tool.value}**.\n",

        "---\n",
        "## 3. Results\n",
        "### 3.1 PRISMA Flow (Item 16a)\n",
        "| Stage | Count |",
        "|-------|-------|",
        f"| PubMed | {f.db_pubmed} |",
        f"| bioRxiv | {f.db_biorxiv} |",
        f"| Related articles | {f.db_related} |",
        f"| Citation hops | {f.db_hops} |",
        f"| **Total identified** | **{f.total_identified}** |",
        f"| Duplicates removed | {f.duplicates_removed} |",
        f"| Screened | {f.screened_title_abstract} |",
        f"| Excluded (screening) | {f.excluded_title_abstract} |",
        f"| Full-text assessed | {f.assessed_eligibility} |",
        f"| Excluded (eligibility) | {f.excluded_eligibility} |",
        f"| **Included** | **{f.included_synthesis}** |\n",
    ])

    # Study characteristics table
    lines.append("### 3.2 Study Characteristics (Item 17)\n")
    lines.append("| Authors | Year | Journal | Design | RoB |")
    lines.append("|---------|------|---------|--------|-----|")
    for a in included:
        design = a.extracted_data.study_design if a.extracted_data else "NR"
        rob = a.risk_of_bias.overall.value if a.risk_of_bias else "NR"
        lines.append(
            f"| {a.short_author} | {a.year} | {a.journal[:30]} | {design} | {rob} |"
        )

    # Synthesis
    lines.extend(["\n### 3.3 Synthesis\n", result.synthesis_text or "[Synthesis pending]"])

    # Risk of bias
    if result.bias_assessment:
        lines.extend(["\n### 3.4 Risk of Bias Assessment (Items 18, 21)\n", result.bias_assessment])

    # GRADE
    if result.grade_assessments:
        lines.append("\n### 3.5 Certainty of Evidence - GRADE (Item 22)\n")
        for outcome, grade in result.grade_assessments.items():
            lines.append(f"**{outcome}:** {grade.overall_certainty.value}")
            lines.append(f"  {grade.summary}\n")

    # Limitations
    lines.extend(["\n---\n", "## 4. Discussion\n"])
    if result.limitations:
        lines.extend(["### 4.1 Limitations\n", result.limitations])

    # Other information
    lines.extend([
        "\n---\n",
        "## 5. Other Information\n",
        f"- **Registration:** {p.registration_number or 'Not registered'}",
        f"- **Protocol:** {p.protocol_url or 'Not prepared'}",
        f"- **Funding:** {p.funding_sources or 'None declared'}",
        f"- **Competing interests:** {p.competing_interests or 'None declared'}",
        f"- **Amendments:** {p.amendments or 'None'}\n",
    ])

    # References
    lines.append("\n## References\n")
    for i, a in enumerate(included, 1):
        lines.append(f"{i}. {a.citation}\n")

    # Evidence spans
    if result.evidence_spans:
        lines.append("\n## Appendix: Evidence Spans\n")
        for e in result.evidence_spans[:20]:
            lines.append(
                f"- **PMID:{e.paper_pmid}** (score: {e.relevance_score:.2f}): "
                f'"{e.text[:200]}..."'
            )

    # Rich structured PrismaReview sections — appended when available
    if result.prisma_review is not None:
        pr = result.prisma_review
        lines.extend([
            "\n---\n",
            "## Abstract\n",
            f"**Background:** {pr.abstract.background}\n",
            f"**Objective:** {pr.abstract.objective}\n",
            f"**Methods:** {pr.abstract.methods}\n",
            f"**Results:** {pr.abstract.results}\n",
            f"**Conclusion:** {pr.abstract.conclusion}\n",
            "\n---\n",
            "## Introduction\n",
            f"### Background\n\n{pr.introduction.background}\n",
            f"### Problem Statement\n\n{pr.introduction.problem_statement}\n",
            f"### Research Gap\n\n{pr.introduction.research_gap}\n",
            f"### Objectives\n\n{pr.introduction.objectives}\n",
            "\n---\n",
            "## Methods\n",
            f"### Search Strategy\n\n{pr.methods.search_strategy}\n",
            f"### Inclusion Criteria\n\n"
            + "\n".join(f"- {c}" for c in pr.methods.inclusion_criteria) + "\n",
            f"### Exclusion Criteria\n\n"
            + "\n".join(f"- {c}" for c in pr.methods.exclusion_criteria) + "\n",
            f"### Quality Assessment\n\n{pr.methods.quality_assessment}\n",
        ])
        if pr.methods.data_extraction:
            lines.append("### Data Extraction\n")
            for report in pr.methods.data_extraction:
                lines.append(f"**{report.source_id}**: {', '.join(report.sections.keys())}")
            lines.append(
                "\n*(See `to_rubric_markdown()` for full structured per-section content.)*\n"
            )
        lines.extend([
            "\n---\n",
            "## Results\n",
        ])
        for theme in pr.results.themes:
            lines.extend([
                f"### Theme: {theme.theme_name}\n",
                f"{theme.description}\n",
                "**Key findings:**",
            ])
            for kf in theme.key_findings:
                lines.append(f"- {kf}")
            lines.append("")

        if pr.results.paragraph_summary:
            lines.append("### Synthesis\n")
            for block in pr.results.paragraph_summary:
                if block.heading:
                    lines.append(f"**{block.heading}**\n")
                lines.append(f"{block.text}\n")

        if pr.results.quantitative_analysis:
            qa = pr.results.quantitative_analysis
            lines.extend([
                "### Quantitative Analysis\n",
                f"**Effect size:** {qa.effect_size or 'N/A'}\n",
                f"**Confidence intervals:** {qa.confidence_intervals or 'N/A'}\n",
                f"**Heterogeneity:** {qa.heterogeneity or 'N/A'}\n",
            ])

        lines.extend([
            "\n---\n",
            "## Discussion\n",
            f"### Summary of Findings\n\n{pr.discussion.summary_of_findings}\n",
            f"### Interpretation\n\n{pr.discussion.interpretation}\n",
            f"### Comparison with Literature\n\n{pr.discussion.comparison_with_literature}\n",
            "### Implications\n",
            f"**Clinical:** {pr.discussion.implications.clinical}\n",
            f"**Policy:** {pr.discussion.implications.policy}\n",
            f"**Research:** {pr.discussion.implications.research}\n",
            f"### Limitations\n\n{pr.discussion.limitations}\n",
            "\n---\n",
            "## Conclusion\n",
            f"### Key Takeaways\n\n{pr.conclusion.key_takeaways}\n",
            f"### Recommendations\n\n{pr.conclusion.recommendations}\n",
            f"### Future Research\n\n{pr.conclusion.future_research}\n",
            "\n---\n",
            "## References\n",
        ])
        for i, ref in enumerate(pr.references, 1):
            lines.append(f"{i}. {ref}\n")

    lines.append(
        f"\n---\n*Generated by PRISMA Agent on {result.timestamp[:10]}. "
        f"AI-assisted components should be verified before publication.*"
    )
    return "\n".join(lines)


def to_bibtex(result: PRISMAReviewResult) -> str:
    """Export included studies as BibTeX."""
    entries = []
    for a in result.included_articles:
        first_author = (
            re.sub(r"[^a-zA-Z]", "", a.authors.split(",")[0])
            if a.authors else "Unknown"
        )
        key = f"{first_author}{a.year}"
        entries.append(
            f"@article{{{key},\n"
            f"  title     = {{{a.title}}},\n"
            f"  author    = {{{a.authors}}},\n"
            f"  journal   = {{{a.journal}}},\n"
            f"  year      = {{{a.year}}},\n"
            f"  doi       = {{{a.doi}}},\n"
            f"  pmid      = {{{a.pmid}}},\n"
            f"}}\n"
        )
    return "\n".join(entries)


def to_json(result: PRISMAReviewResult) -> str:
    """Export review as JSON."""
    return result.model_dump_json(indent=2)


def to_turtle(result: PRISMAReviewResult) -> str:
    """Serialize review to Turtle RDF format (SLR Ontology)."""
    from .ontology.rdf_export import to_turtle as _to_turtle
    return _to_turtle(result)


def to_jsonld(result: PRISMAReviewResult) -> str:
    """Serialize review to JSON-LD format (SLR Ontology)."""
    from .ontology.rdf_export import to_jsonld as _to_jsonld
    return _to_jsonld(result)


def to_rubric_markdown(result: PRISMAReviewResult) -> str:
    """Export all DataChartingRubric section_outputs as structured Markdown.

    One H2 per study, one H3 per section, with formatted_answer and summary.
    Falls back to a minimal document when no section_outputs are present.
    """
    rubrics_with_outputs = [r for r in result.data_charting_rubrics if r.section_outputs]
    if not rubrics_with_outputs:
        return "# Data Extraction Rubrics\n\n*No structured section outputs available.*\n"

    lines = ["# Data Extraction Rubrics\n"]
    for rubric in rubrics_with_outputs:
        lines.append(f"## {rubric.source_id} — {rubric.title or 'Unknown'}\n")
        for section_title, section_out in rubric.section_outputs.items():
            lines.append(f"### {section_title}\n")
            lines.append(f"**Format**: {section_out.format_used}\n")
            lines.append(section_out.formatted_answer)
            lines.append("")
            if section_out.section_summary:
                lines.append(f"**Summary**: {section_out.section_summary}\n")
        lines.append("---\n")
    return "\n".join(lines)


def to_rubric_json(result: PRISMAReviewResult) -> str:
    """Export all DataChartingRubric section_outputs as structured JSON.

    Returns a JSON array, one object per study. Falls back to '[]' when
    no section_outputs are present.
    """
    rubrics_with_outputs = [r for r in result.data_charting_rubrics if r.section_outputs]
    if not rubrics_with_outputs:
        return "[]"

    records = [
        {
            "source_id": rubric.source_id,
            "title": rubric.title or "",
            "sections": {
                title: {
                    "format_used": out.format_used,
                    "formatted_answer": out.formatted_answer,
                    "section_summary": out.section_summary,
                }
                for title, out in rubric.section_outputs.items()
            },
        }
        for rubric in rubrics_with_outputs
    ]
    return json.dumps(records, indent=2, ensure_ascii=False)


# ──────────────────── Feature 006: Field-Level Export Functions ───────────────


def _get_data_extraction(result: PRISMAReviewResult) -> list:
    """Return data_extraction list from PrismaReview.methods, or empty list."""
    pr = result.prisma_review
    if pr and pr.methods and pr.methods.data_extraction:
        return pr.methods.data_extraction
    return []


def _get_appraisal_results(result: PRISMAReviewResult) -> list:
    """Return critical_appraisal_results from PrismaReview.methods, or empty list."""
    pr = result.prisma_review
    if pr and pr.methods and pr.methods.critical_appraisal_results:
        return pr.methods.critical_appraisal_results
    # Fall back to structured_appraisal_results on PRISMAReviewResult itself
    return getattr(result, "structured_appraisal_results", []) or []


def to_charting_markdown(result: PRISMAReviewResult) -> str:
    """Export per-study field-level extraction results as structured Markdown.

    One H2 per study, one H3 per section, with a field/answer/confidence table.
    Falls back to a note when no field_answers are available.
    """
    extraction_reports = _get_data_extraction(result)
    reports_with_fields = [r for r in extraction_reports if r.field_answers]

    if not reports_with_fields:
        return (
            "# Data Charting: Field-Level Extraction\n\n"
            "*Field-level extraction not available (no ChartingTemplate was applied).*\n"
        )

    lines = ["# Data Charting: Field-Level Extraction\n"]
    for report in reports_with_fields:
        title = ""
        if result.prisma_review:
            for rubric in result.data_charting_rubrics:
                if rubric.source_id == report.source_id:
                    title = rubric.title or ""
                    break
        heading = f"## {report.source_id}" + (f" — {title}" if title else "")
        lines.append(heading + "\n")

        for section_key in sorted(report.field_answers.keys()):
            section_result = report.field_answers[section_key]
            lines.append(f"### {section_result.section_title} (Section {section_key})\n")
            lines.append("| Field | Answer | Confidence |")
            lines.append("|-------|--------|------------|")
            for fa in section_result.field_answers:
                if fa.value is None:
                    value_cell = "_[Human reviewer]_"
                    conf_cell = "—"
                else:
                    value_cell = fa.value.replace("|", "\\|")
                    conf_cell = fa.confidence
                lines.append(f"| {fa.field_name} | {value_cell} | {conf_cell} |")
            lines.append("")
        lines.append("---\n")
    return "\n".join(lines)


def to_charting_json(result: PRISMAReviewResult) -> str:
    """Export per-study field-level extraction results as JSON.

    Returns a JSON array with one object per study, keyed by section_key.
    """
    extraction_reports = _get_data_extraction(result)

    records = []
    for report in extraction_reports:
        title = ""
        for rubric in result.data_charting_rubrics:
            if rubric.source_id == report.source_id:
                title = rubric.title or ""
                break

        charting: dict = {}
        for section_key, section_result in report.field_answers.items():
            fields_out = []
            for fa in section_result.field_answers:
                if fa.value is None:
                    fields_out.append({
                        "field_name": fa.field_name,
                        "value": None,
                        "reviewer_only": True,
                    })
                else:
                    fields_out.append({
                        "field_name": fa.field_name,
                        "value": fa.value,
                        "confidence": fa.confidence,
                        "extraction_note": fa.extraction_note,
                    })
            charting[section_key] = {
                "section_title": section_result.section_title,
                "fields": fields_out,
            }

        records.append({
            "source_id": report.source_id,
            "title": title,
            "charting": charting,
        })

    return json.dumps(records, indent=2, ensure_ascii=False)


def to_appraisal_markdown(result: PRISMAReviewResult) -> str:
    """Export critical appraisal results as structured Markdown.

    One H2 per study, one H3 per domain, plus a cross-study summary table.
    Falls back to a note when no appraisal results are available.
    """
    appraisal_results = _get_appraisal_results(result)

    if not appraisal_results:
        return (
            "# Critical Appraisal Results\n\n"
            "*No critical appraisal results available.*\n"
        )

    lines = ["# Critical Appraisal Results\n"]

    # Per-study sections
    for appraisal in appraisal_results:
        title = ""
        for rubric in result.data_charting_rubrics:
            if rubric.source_id == appraisal.source_id:
                title = rubric.title or ""
                break
        heading = f"## {appraisal.source_id}" + (f" — {title}" if title else "")
        lines.append(heading + "\n")

        for i, domain in enumerate(appraisal.domains, 1):
            lines.append(f"### Domain {i}: {domain.domain_name}\n")
            lines.append("| Item | Rating |")
            lines.append("|------|--------|")
            for item_rating in domain.item_ratings:
                lines.append(f"| {item_rating.item_text} | {item_rating.rating} |")
            lines.append("")
            lines.append(f"**Overall Concern**: {domain.domain_concern}\n")

        lines.append("---\n")

    # Cross-study summary table
    lines.append("## Cross-Study Appraisal Summary\n")

    # Collect all domain names in order from first study
    all_domains: list[str] = []
    if appraisal_results:
        all_domains = [d.domain_name for d in appraisal_results[0].domains]

    if all_domains:
        lines.append("| Domain | Low | Some | High | Total |")
        lines.append("|--------|-----|------|------|-------|")
        for domain_name in all_domains:
            counts: dict[str, int] = {"Low": 0, "Some": 0, "High": 0}
            for appraisal in appraisal_results:
                for domain in appraisal.domains:
                    if domain.domain_name == domain_name:
                        counts[domain.domain_concern] = counts.get(domain.domain_concern, 0) + 1
            total = sum(counts.values())
            pct = lambda n: f"{n} ({n/total:.0%})" if total > 0 else str(n)
            lines.append(
                f"| {domain_name} | {pct(counts['Low'])} | {pct(counts['Some'])} | {pct(counts['High'])} | {total} |"
            )
        lines.append("")

    return "\n".join(lines)


def to_appraisal_json(result: PRISMAReviewResult) -> str:
    """Export critical appraisal results as JSON.

    Returns a JSON object with 'studies' (per-study appraisals) and
    'summary' (aggregated concern counts per domain).
    """
    appraisal_results = _get_appraisal_results(result)

    studies = []
    summary: dict[str, dict[str, int]] = {}

    for appraisal in appraisal_results:
        appraisal_data = []
        for domain in appraisal.domains:
            domain_entry = {
                "domain_name": domain.domain_name,
                "domain_concern": domain.domain_concern,
                "items": [
                    {"item_text": ir.item_text, "rating": ir.rating}
                    for ir in domain.item_ratings
                ],
            }
            appraisal_data.append(domain_entry)

            # Accumulate summary counts
            if domain.domain_name not in summary:
                summary[domain.domain_name] = {"Low": 0, "Some": 0, "High": 0, "total": 0}
            summary[domain.domain_name][domain.domain_concern] = (
                summary[domain.domain_name].get(domain.domain_concern, 0) + 1
            )
            summary[domain.domain_name]["total"] += 1

        studies.append({
            "source_id": appraisal.source_id,
            "appraisal": appraisal_data,
        })


# ────────────────── Feature 007: Compare-mode exports ──────────────────


def to_compare_markdown(result: CompareReviewResult) -> str:
    """Export compare-mode results as Markdown with per-model sections and merged consensus."""
    p = result.protocol
    lines = [
        f"# {p.title or 'Systematic Review'} — Multi-Model Compare",
        f"\n*Generated: {result.timestamp} | Models: {', '.join(result.compare_models)}*\n",
        "---\n",
    ]

    # Run summary table
    lines.append("## Run Summary\n")
    lines.append("| Model | Status | Included | Evidence Spans |")
    lines.append("|-------|--------|----------|----------------|")
    for run in result.model_results:
        status = "✓ Success" if run.succeeded else "✗ Failed"
        included = len(run.result.included_articles or []) if run.succeeded and run.result else "—"
        spans = len(run.result.evidence_spans or []) if run.succeeded and run.result else "—"
        lines.append(f"| {run.model_name} | {status} | {included} | {spans} |")
    lines.append("")

    # Per-model sections
    for run in result.model_results:
        lines.append(f"\n---\n\n## Model: {run.model_name}\n")
        if run.succeeded and run.result:
            lines.append(to_markdown(run.result))
        else:
            lines.append(f"> ⚠ **Run Failed**: {run.error or 'unknown error'}\n")

    # Merged consensus section
    lines.append("\n---\n\n## Merged — Consensus & Divergences\n")
    lines.append("### Consensus Synthesis\n")
    lines.append(result.merged.consensus_synthesis or "_No consensus synthesis available._")
    lines.append("")

    if result.merged.synthesis_divergences:
        lines.append("\n### Notable Divergences\n")
        lines.append("| Topic | " + " | ".join(result.compare_models) + " |")
        lines.append("|-------|" + "--------|" * len(result.compare_models))
        for div in result.merged.synthesis_divergences:
            row = f"| {div.topic} |"
            for m in result.compare_models:
                pos = div.positions.get(m, "_no data_")
                row += f" {pos[:120]} |"
            lines.append(row)
        lines.append("")

    return "\n".join(lines)


def to_compare_json(result: CompareReviewResult) -> str:
    """Export compare-mode results as JSON."""
    return result.model_dump_json(indent=2)


def to_compare_charting_markdown(result: CompareReviewResult) -> str:
    """Export side-by-side charting comparison as Markdown tables."""
    p = result.protocol
    lines = [
        f"# {p.title or 'Systematic Review'} — Charting Comparison",
        f"\n*Models: {', '.join(result.compare_models)}*\n",
    ]

    succeeded = [r for r in result.model_results if r.succeeded and r.result]

    if not succeeded:
        lines.append("> No successful model runs to compare.\n")
        return "\n".join(lines)

    # Gather all source_ids from any model
    all_source_ids: list[str] = []
    seen_ids: set[str] = set()
    for run in succeeded:
        for rubric in (run.result.data_charting_rubrics if run.result else []):
            if rubric.source_id not in seen_ids:
                all_source_ids.append(rubric.source_id)
                seen_ids.add(rubric.source_id)

    if not all_source_ids:
        lines.append("> No charting data available.\n")
        return "\n".join(lines)

    model_names = [r.model_name for r in succeeded]

    for source_id in all_source_ids:
        lines.append(f"\n## Study: {source_id}\n")

        # Gather sections present in any model for this study
        section_keys: list[str] = []
        seen_secs: set[str] = set()
        for run in succeeded:
            rubric = next(
                (r for r in (run.result.data_charting_rubrics if run.result else []) if r.source_id == source_id),
                None,
            )
            if rubric:
                for sk in rubric.field_answers.keys():
                    if sk not in seen_secs:
                        section_keys.append(sk)
                        seen_secs.add(sk)

        for section_key in section_keys:
            lines.append(f"### Section: {section_key}\n")
            header = "| Field | " + " | ".join(model_names) + " | Agreement |"
            sep = "|-------|" + "--------|" * len(model_names) + "-----------|"
            lines.append(header)
            lines.append(sep)

            # Gather all field names in this section across models
            field_names: list[str] = []
            seen_fields: set[str] = set()
            for run in succeeded:
                rubric = next(
                    (r for r in (run.result.data_charting_rubrics if run.result else []) if r.source_id == source_id),
                    None,
                )
                if rubric and section_key in rubric.field_answers:
                    sec_res = rubric.field_answers[section_key]
                    if hasattr(sec_res, "field_answers"):
                        for fa in sec_res.field_answers:
                            if fa.field_name not in seen_fields:
                                field_names.append(fa.field_name)
                                seen_fields.add(fa.field_name)

            for field_name in field_names:
                key = f"{source_id}::{section_key}::{field_name}"
                fa_entry = result.merged.field_agreement.get(key)

                row_values: list[str] = []
                for run in succeeded:
                    rubric = next(
                        (r for r in (run.result.data_charting_rubrics if run.result else []) if r.source_id == source_id),
                        None,
                    )
                    val = "_—_"
                    if rubric and section_key in rubric.field_answers:
                        sec_res = rubric.field_answers[section_key]
                        if hasattr(sec_res, "field_answers"):
                            for fa in sec_res.field_answers:
                                if fa.field_name == field_name:
                                    val = (fa.value or "").replace("|", "\\|")[:80]
                                    break
                    row_values.append(val)

                if fa_entry is not None:
                    agreement_cell = "✓ Agree" if fa_entry.agreed else "⚠ Differ"
                else:
                    agreement_cell = "—"

                row = f"| {field_name} | " + " | ".join(row_values) + f" | {agreement_cell} |"
                lines.append(row)

            lines.append("")

    return "\n".join(lines)


def to_compare_charting_json(result: CompareReviewResult) -> str:
    """Export charting comparison as JSON with per-field agreement indicators."""
    succeeded = [r for r in result.model_results if r.succeeded and r.result]

    studies: list[dict] = []

    all_source_ids: list[str] = []
    seen_ids: set[str] = set()
    # Collect source_ids from rubrics
    for run in succeeded:
        for rubric in (run.result.data_charting_rubrics if run.result else []):
            if rubric.source_id not in seen_ids:
                all_source_ids.append(rubric.source_id)
                seen_ids.add(rubric.source_id)
    # Also collect source_ids from field_agreement keys (covers cases with no rubrics)
    for key in result.merged.field_agreement:
        sid = key.split("::")[0]
        if sid not in seen_ids:
            all_source_ids.append(sid)
            seen_ids.add(sid)

    for source_id in all_source_ids:
        fields_data: list[dict] = []

        key_prefix = f"{source_id}::"
        for key, fa in result.merged.field_agreement.items():
            if not key.startswith(key_prefix):
                continue
            _, section_key, field_name = key.split("::", 2)
            fields_data.append({
                "section_key": section_key,
                "field_name": field_name,
                "values": fa.values,
                "agreed": fa.agreed,
            })

        studies.append({"source_id": source_id, "fields": fields_data})

    return json.dumps({"compare_models": result.compare_models, "studies": studies}, indent=2)

    return json.dumps({"studies": studies, "summary": summary}, indent=2, ensure_ascii=False)
