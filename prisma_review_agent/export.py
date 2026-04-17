"""
Export functions for PRISMA review results.

Supports Markdown (PRISMA 2020 format), JSON, BibTeX, Turtle, JSON-LD,
and a queryable pyoxigraph RDF store.
"""

from __future__ import annotations

import re
import json

from .models import PRISMAReviewResult
from .ontology.rdf_export import to_turtle, to_jsonld  # noqa: F401 — re-exported
from .ontology.rdf_store import SLRStore  # noqa: F401 — re-exported

__all__ = ["to_markdown", "to_bibtex", "to_json", "to_turtle", "to_jsonld", "to_oxigraph_store"]


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
