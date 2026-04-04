"""
Export functions for PRISMA review results.

Supports Markdown (PRISMA 2020 format), JSON, and BibTeX.
"""

from __future__ import annotations

import re
import json

from .models import PRISMAReviewResult


def to_markdown(result: PRISMAReviewResult) -> str:
    """Export review as PRISMA 2020 structured Markdown."""
    p = result.protocol
    f = result.flow
    included = result.included_articles

    lines = [
        f"# {p.title or 'Systematic Review'}",
        f"\n*Generated: {result.timestamp} | PRISMA 2020 Compliant*\n",
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
