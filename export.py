"""
Export functions for PRISMA review results.

Supports Markdown (PRISMA 2020 format), JSON, and BibTeX.
"""

from __future__ import annotations

import re
import json
from datetime import datetime

from models import PRISMAReviewResult


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


def to_data_charting_csv(result: PRISMAReviewResult) -> str:
    """Export data charting rubrics as CSV."""
    if not result.data_charting_rubrics:
        return "No data charting rubrics available"

    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    # Header row
    headers = [
        "Source ID", "Title", "Authors", "Year", "Journal/Conference", "DOI",
        "Database Retrieved From", "Disorder Cohort", "Primary Focus",
        "Primary Study Goal", "Study Design", "Duration and Frequency",
        "Subject Model", "Task Type", "Study Setting", "Country or Region",
        "Disorder/Diagnosis", "Diagnosis Assessment", "N (Disordered)",
        "Age Mean (SD)", "Age Range", "Gender Distribution",
        "Comorbidities Included/Excluded", "Medications/Therapies",
        "Severity Levels Included", "Healthy Controls Included",
        "Healthy Status Confirmed", "N (Controls)", "Age Mean (SD) Controls",
        "Age Range Controls", "Gender Distribution Controls", "Age-Matched",
        "Gender-Matched", "Neurodevelopmentally Typical", "Data Types Collected",
        "Tasks Performed", "Equipment/Tools Used", "New Dataset Contributed",
        "Dataset Openly Available", "Dataset Available on Request",
        "Sensitive Data Anonymized", "Feature Types", "Specific Features",
        "Feature Extraction Tools", "Feature Importance Reported",
        "Importance Method", "Top Features Identified", "Direction of Feature Change",
        "Model Category", "Specific Algorithms", "Validation Methodology",
        "Performance Metrics", "Key Performance Results", "Summary of Key Findings",
        "Features Most Associated With Disorder", "Future Directions Recommended",
        "Reviewer Notes"
    ]
    writer.writerow(headers)

    # Data rows
    for rubric in result.data_charting_rubrics:
        row = [
            rubric.source_id, rubric.title, rubric.authors, rubric.year,
            rubric.journal_conference, rubric.doi, rubric.database_retrieved,
            rubric.disorder_cohort, rubric.primary_focus, rubric.primary_goal,
            rubric.study_design, rubric.duration_frequency, rubric.subject_model,
            rubric.task_type, rubric.study_setting, rubric.country_region,
            rubric.disorder_diagnosis, rubric.diagnosis_assessment, rubric.n_disordered,
            rubric.age_mean_sd, rubric.age_range, rubric.gender_distribution,
            rubric.comorbidities_included_excluded, rubric.medications_therapies,
            rubric.severity_levels, rubric.healthy_controls_included,
            rubric.healthy_status_confirmed, rubric.n_controls, rubric.age_mean_sd_controls,
            rubric.age_range_controls, rubric.gender_distribution_controls,
            rubric.age_matched, rubric.gender_matched, rubric.neurodevelopmentally_typical,
            rubric.data_types, rubric.tasks_performed, rubric.equipment_tools,
            rubric.new_dataset_contributed, rubric.dataset_openly_available,
            rubric.dataset_available_request, rubric.sensitive_data_anonymized,
            rubric.feature_types, rubric.specific_features, rubric.feature_extraction_tools,
            rubric.feature_importance_reported, rubric.importance_method,
            rubric.top_features_identified, rubric.feature_change_direction,
            rubric.model_category, rubric.specific_algorithms, rubric.validation_methodology,
            rubric.performance_metrics, rubric.key_performance_results,
            rubric.summary_key_findings, rubric.features_associated_disorder,
            rubric.future_directions_recommended, rubric.reviewer_notes
        ]
        writer.writerow(row)

    return output.getvalue()


def to_narrative_csv(result: PRISMAReviewResult) -> str:
    """Export PRISMA narrative rows as CSV."""
    if not result.narrative_rows:
        return "No narrative rows available"

    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    # Header row
    headers = [
        "Source ID", "Study design / sample / dataset",
        "Methods", "Outcomes", "Key limitations", "Relevance notes",
        "Review-specific questions"
    ]
    writer.writerow(headers)

    # Data rows
    for row in result.narrative_rows:
        writer.writerow([
            row.source_id, row.study_design_sample_dataset, row.methods,
            row.outcomes, row.key_limitations, row.relevance_notes,
            row.review_specific_questions
        ])

    return output.getvalue()


def to_appraisal_csv(result: PRISMAReviewResult) -> str:
    """Export critical appraisals as CSV."""
    if not result.critical_appraisals:
        return "No critical appraisals available"

    import csv
    from io import StringIO

    output = StringIO()
    writer = csv.writer(output)

    # Header row
    headers = [
        "Source ID",
        # Domain 1
        "D1_Item1_Rating", "D1_Item1_Notes",
        "D1_Item2_Rating", "D1_Item2_Notes",
        "D1_Item3_Rating", "D1_Item3_Notes",
        "D1_Item4_Rating", "D1_Item4_Notes",
        "D1_Item5_Rating", "D1_Item5_Notes",
        "D1_Overall_Concern",
        # Domain 2
        "D2_Item1_Rating", "D2_Item1_Notes",
        "D2_Item2_Rating", "D2_Item2_Notes",
        "D2_Item3_Rating", "D2_Item3_Notes",
        "D2_Overall_Concern",
        # Domain 3
        "D3_Item1_Rating", "D3_Item1_Notes",
        "D3_Item2_Rating", "D3_Item2_Notes",
        "D3_Item3_Rating", "D3_Item3_Notes",
        "D3_Item4_Rating", "D3_Item4_Notes",
        "D3_Item5_Rating", "D3_Item5_Notes",
        "D3_Overall_Concern",
        # Domain 4
        "D4_Item1_Rating", "D4_Item1_Notes",
        "D4_Item2_Rating", "D4_Item2_Notes",
        "D4_Item3_Rating", "D4_Item3_Notes",
        "D4_Item4_Rating", "D4_Item4_Notes",
        "D4_Overall_Concern",
        # Overall
        "Overall_Concern_Score"
    ]
    writer.writerow(headers)

    # Data rows
    for appraisal in result.critical_appraisals:
        row = [appraisal.source_id]

        # Domain 1
        for item in appraisal.domain_1_participant_quality.items:
            row.extend([item.rating, item.notes])
        row.append(appraisal.domain_1_participant_quality.overall_concern)

        # Domain 2
        for item in appraisal.domain_2_data_collection_quality.items:
            row.extend([item.rating, item.notes])
        row.append(appraisal.domain_2_data_collection_quality.overall_concern)

        # Domain 3
        for item in appraisal.domain_3_feature_model_quality.items:
            row.extend([item.rating, item.notes])
        row.append(appraisal.domain_3_feature_model_quality.overall_concern)

        # Domain 4
        for item in appraisal.domain_4_bias_transparency.items:
            row.extend([item.rating, item.notes])
        row.append(appraisal.domain_4_bias_transparency.overall_concern)

        # Overall
        row.append(appraisal.overall_concern_score)

        writer.writerow(row)

    return output.getvalue()


def to_source_json(result: PRISMAReviewResult) -> str:
    """Emit per-source JSON records matching the Section 4 schema from the SLR brief."""
    records = []
    for rubric in result.data_charting_rubrics:
        appraisal = next(
            (a for a in result.critical_appraisals if a.source_id == rubric.source_id),
            None,
        )

        def _domain_dict(domain) -> dict:
            return {
                "items": {item.item_text: item.rating for item in domain.items},
                "overall_concern": domain.overall_concern,
                "justification": "; ".join(
                    f"{item.item_text}: {item.notes}" for item in domain.items if item.notes
                ),
            }

        records.append({
            "source_id": rubric.source_id,
            "section_a": {
                "title": rubric.title,
                "authors": rubric.authors,
                "year": rubric.year,
                "venue": rubric.journal_conference,
                "doi": rubric.doi,
                "database_retrieved": rubric.database_retrieved,
                "review_cohort_category": rubric.disorder_cohort,
                "primary_focus": rubric.primary_focus,
            },
            "section_b": {
                "primary_study_goal": rubric.primary_goal,
                "study_design": rubric.study_design,
                "duration_frequency": rubric.duration_frequency,
                "subject_model": rubric.subject_model,
                "task_type": rubric.task_type,
                "study_setting": rubric.study_setting,
                "country_region": rubric.country_region,
            },
            "section_c": {
                "population_condition": rubric.disorder_diagnosis,
                "inclusion_assessment": rubric.diagnosis_assessment,
                "n_primary": rubric.n_disordered,
                "age_mean_sd": rubric.age_mean_sd,
                "age_range": rubric.age_range,
                "gender_distribution": rubric.gender_distribution,
                "comorbidities": rubric.comorbidities_included_excluded,
                "medications_therapies": rubric.medications_therapies,
                "severity_levels": rubric.severity_levels,
            },
            "section_d": {
                "comparison_group_included": rubric.healthy_controls_included,
                "comparison_status_confirmed": rubric.healthy_status_confirmed,
                "n_comparison": rubric.n_controls,
                "age_mean_sd_comparison": rubric.age_mean_sd_controls,
                "age_range_comparison": rubric.age_range_controls,
                "gender_comparison": rubric.gender_distribution_controls,
                "age_matched": rubric.age_matched,
                "gender_matched": rubric.gender_matched,
                "additional_matching": rubric.neurodevelopmentally_typical,
            },
            "section_e": {
                "data_types_collected": rubric.data_types,
                "tasks_procedures": rubric.tasks_performed,
                "equipment_tools": rubric.equipment_tools,
                "new_dataset_contributed": rubric.new_dataset_contributed,
                "dataset_openly_available": rubric.dataset_openly_available,
                "dataset_available_on_request": rubric.dataset_available_request,
                "sensitive_data_anonymized": rubric.sensitive_data_anonymized,
            },
            "section_f": {
                "feature_types": rubric.feature_types,
                "specific_features": rubric.specific_features,
                "extraction_tools": rubric.feature_extraction_tools,
                "feature_importance_reported": rubric.feature_importance_reported,
                "importance_method": rubric.importance_method,
                "top_features": rubric.top_features_identified,
                "direction_of_effect": rubric.feature_change_direction,
                "method_category": rubric.model_category,
                "specific_algorithms": rubric.specific_algorithms,
                "validation_methodology": rubric.validation_methodology,
                "performance_metrics": rubric.performance_metrics,
                "key_results": rubric.key_performance_results,
            },
            "section_g": {
                "summary": rubric.summary_key_findings,
                "features_associated": rubric.features_associated_disorder,
                "future_directions": rubric.future_directions_recommended,
                "reviewer_notes": rubric.reviewer_notes,
            },
            "appraisal": {
                "domain_1": _domain_dict(appraisal.domain_1_participant_quality) if appraisal else {},
                "domain_2": _domain_dict(appraisal.domain_2_data_collection_quality) if appraisal else {},
                "domain_3": _domain_dict(appraisal.domain_3_feature_model_quality) if appraisal else {},
                "domain_4": _domain_dict(appraisal.domain_4_bias_transparency) if appraisal else {},
                "overall_concern": appraisal.overall_concern_score if appraisal else "Not Assessed",
            } if appraisal else {},
            "flags": [rubric.reviewer_notes] if rubric.reviewer_notes else [],
        })
    return json.dumps(records, indent=2, ensure_ascii=False)


def to_enhanced_markdown(result: PRISMAReviewResult) -> str:
    """
    Export review as a full structured Markdown document following the SLR Brief
    sections 2.1–2.9 (Title Page → Appendices A–G). PRISMA 2020 compliant.
    """
    p = result.protocol
    f = result.flow
    included = result.included_articles
    ts = result.timestamp[:10] if result.timestamp else datetime.now().strftime("%Y-%m-%d")
    citation_style = getattr(p, "citation_style", "APA 7")
    languages = getattr(p, "languages", ["English"])
    grey_lit = getattr(p, "grey_literature_sources", [])
    target_audience = getattr(p, "target_audience", "academic journal") or "academic journal"

    lines: list[str] = []

    # ── §2.1 TITLE PAGE ──────────────────────────────────────────────────
    lines += [
        "---",
        f"# {p.title or 'Systematic Literature Review'}",
        "### A Systematic Review",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Date** | {ts} |",
        f"| **Version** | 1.0 |",
        f"| **Registration** | {p.registration_number or 'Not registered'} |",
        f"| **Protocol URL** | {p.protocol_url or 'Not available'} |",
        f"| **Target Audience** | {target_audience} |",
        f"| **Citation Style** | {citation_style} |",
        f"| **Funding** | {p.funding_sources or 'None declared'} |",
        f"| **Competing Interests** | {p.competing_interests or 'None declared'} |",
        "",
        "---",
        "",
    ]

    # ── §2.2 STRUCTURED ABSTRACT ─────────────────────────────────────────
    lines += ["## Abstract\n", "*(PRISMA-Abstract 12-item checklist)*\n"]
    if result.structured_abstract:
        lines.append(result.structured_abstract)
    else:
        # Fallback when abstract agent hasn't run yet
        lines += [
            f"**Background:** {p.objective or p.title}",
            "",
            f"**Objectives:** {p.question}",
            "",
            f"**Methods:** Systematic search of {', '.join(p.databases)}"
            + (f" and grey literature ({', '.join(grey_lit)})" if grey_lit else "")
            + f". Studies screened against PICOS criteria. Risk of bias assessed using {p.rob_tool.value}.",
            "",
            f"**Results:** {f.total_identified} records identified. After removing "
            f"{f.duplicates_removed} duplicates and screening, {f.included_synthesis} studies included.",
            "",
            "**Conclusions:** [See Section 5]",
            "",
            f"**Registration:** {p.registration_number or 'Not registered'}",
            "",
            f"**Keywords:** {', '.join(filter(None, [p.pico_population, p.pico_intervention, p.pico_outcome]))[:5]}",
        ]
    lines += ["", "---", ""]

    # ── §2.3 INTRODUCTION ────────────────────────────────────────────────
    lines += ["## 1. Introduction\n"]
    if result.introduction_text:
        lines.append(result.introduction_text)
    else:
        lines += [
            "### 1.1 Background and Context\n",
            p.objective or p.title,
            "",
            "### 1.2 Rationale for the Review\n",
            f"A systematic review of '{p.question}' is warranted to synthesise the available evidence.",
            "",
            "### 1.3 Objectives and Research Questions\n",
            f"**RQ1:** {p.question}",
        ]
    lines += ["", "---", ""]

    # ── §2.4 METHODS ─────────────────────────────────────────────────────
    lines += ["## 2. Methods\n"]

    # 2.4.1 Protocol and registration
    lines += [
        "### 2.1 Protocol and Registration *(PRISMA Item 24a)*\n",
        f"{'A protocol was registered prior to this review.' if p.registration_number else 'No protocol was registered.'} "
        f"Registration: {p.registration_number or 'None'}. "
        f"Amendments: {p.amendments or 'None'}.",
        "",
    ]

    # 2.4.2 Eligibility criteria (PICOS table — 9 dimensions)
    lines += [
        "### 2.2 Eligibility Criteria *(PRISMA Item 5)*\n",
        "| Dimension | Inclusion | Exclusion |",
        "|-----------|-----------|-----------|",
        f"| **Population / Participants** | {p.pico_population or '—'} | — |",
        f"| **Intervention / Exposure** | {p.pico_intervention or '—'} | — |",
        f"| **Comparator / Control** | {p.pico_comparison or 'None / single-arm'} | — |",
        f"| **Outcomes** | {p.pico_outcome or '—'} | — |",
        f"| **Study designs — included** | {p.inclusion_criteria or '—'} | {p.exclusion_criteria or '—'} |",
        f"| **Timeframe** | {p.date_range_start or 'N/A'} – {p.date_range_end or 'present'} | — |",
        f"| **Language** | {', '.join(languages)} | All others |",
        f"| **Setting / Context** | Not restricted | — |",
        f"| **Publication type** | Peer-reviewed; preprints if grey literature enabled | — |",
        "",
    ]

    # 2.4.3 Information sources
    lines += ["### 2.3 Information Sources *(PRISMA Item 6)*\n", "**Databases:**"]
    for db in p.databases:
        lines.append(f"- {db}")
    if grey_lit:
        lines += ["", "**Grey literature / preprint sources:**"]
        for gl in grey_lit:
            lines.append(f"- {gl}")
    lines += [
        "",
        f"Supplementary: backward/forward citation chasing (up to {p.max_hops} hops).",
        f"Date of last search: {ts}",
        "",
    ]

    # 2.4.4 Search strategy
    lines += [
        "### 2.4 Search Strategy *(PRISMA Item 7)*\n",
        "Primary search string (all database strings in Appendix B):\n",
        "```",
    ]
    for q in (result.search_queries or [])[:3]:
        lines.append(q)
    lines += ["```", ""]

    # 2.4.5 Study selection
    lines += [
        "### 2.5 Study Selection Process *(PRISMA Item 8)*\n",
        "De-duplication via DOI-based matching. Two-stage screening: "
        "(1) title/abstract — AI-assisted batch screening; "
        "(2) full-text — strict eligibility against PICOS. "
        "Conflict resolution by third reviewer / consensus discussion.",
        "",
    ]

    # 2.4.6 Data extraction
    lines += [
        "### 2.6 Data Extraction — Per-Source Charting *(PRISMA Item 9)*\n",
        "Each included source charted into a single row using the seven-section template (A–G). "
        "Missing information coded **Not Reported**; ambiguous coded **Unclear** with a reviewer note. "
        "Full spreadsheet in Appendix D.",
        "",
        "| Section | Content |",
        "|---------|---------|",
        "| A | Publication information |",
        "| B | Study design |",
        "| C | Primary sample / group of interest |",
        "| D | Comparison group (if applicable) |",
        "| E | Data collection |",
        "| F | Methods and results |",
        "| G | Synthesis fields (reviewer-completed) |",
        "",
    ]

    # 2.4.7 Risk of bias + critical appraisal
    lines += [
        "### 2.7 Risk of Bias and Critical Appraisal *(PRISMA Items 10–11)*\n",
        f"**Study-design-matched tool:** {p.rob_tool.value}\n",
        "**Four-domain critical appraisal rubric** applied to every included source:\n",
        "| Domain | Scope |",
        "|--------|-------|",
        "| 1 — Sample and Population Quality | Target population definition, inclusion criteria, sample size, matching |",
        "| 2 — Data Collection Quality | Setup description, task standardisation, setting |",
        "| 3 — Methods and Analysis Quality | Variables, extraction method, validation, outcome metrics |",
        "| 4 — Bias and Transparency | Imbalance, limitations, interpretability, data/code availability |",
        "",
        "Each item rated: **Yes / Partial / No / Not Reported / N/A**. "
        "Domain concern: **Low / Some / High**.",
        "",
    ]

    # 2.4.8 Synthesis methods
    lines += [
        "### 2.8 Synthesis Methods *(PRISMA Item 13)*\n",
        "Narrative/qualitative synthesis using thematic synthesis (SWiM guideline). "
        "Certainty of evidence assessed using GRADE per primary outcome.",
        "",
        "---",
        "",
    ]

    # ── §2.5 RESULTS ─────────────────────────────────────────────────────
    lines += ["## 3. Results\n"]

    # 3.1 Study selection / PRISMA flow
    lines += [
        "### 3.1 Study Selection *(PRISMA Item 16a)*\n",
        "| Stage | Count |",
        "|-------|-------|",
        f"| Identified — {', '.join(p.databases[:2])} | {f.db_pubmed + f.db_biorxiv} |",
        f"| Identified — related articles | {f.db_related} |",
        f"| Identified — citation hops | {f.db_hops} |",
        f"| **Total identified** | **{f.total_identified}** |",
        f"| Duplicates removed | {f.duplicates_removed} |",
        f"| After de-duplication | {f.after_dedup} |",
        f"| Screened (title / abstract) | {f.screened_title_abstract} |",
        f"| Excluded (title / abstract) | {f.excluded_title_abstract} |",
        f"| Full-text assessed | {f.assessed_eligibility} |",
        f"| Full-text excluded | {f.excluded_eligibility} |",
        f"| **Included in synthesis** | **{f.included_synthesis}** |",
        "",
    ]

    if f.excluded_reasons:
        lines += ["**Full-text exclusion reasons:**", ""]
        for reason, count in sorted(f.excluded_reasons.items(), key=lambda x: -x[1]):
            lines.append(f"- {reason}: n={count}")
        lines.append("")

    # 3.2 Study characteristics
    years = [a.year for a in included if a.year]
    year_range = f"{min(years)} – {max(years)}" if years else "N/A"
    lines += [
        "### 3.2 Study Characteristics *(PRISMA Item 17)*\n",
        f"*{len(included)} studies included. Publication years: {year_range}.*\n",
        "| Source ID | Authors (Year) | Journal | Design | N | RoB |",
        "|-----------|----------------|---------|--------|---|-----|",
    ]
    for i, a in enumerate(included):
        design = a.extracted_data.study_design if a.extracted_data else "NR"
        rob = a.risk_of_bias.overall.value if a.risk_of_bias else "NR"
        rubric = next(
            (r for r in result.data_charting_rubrics if r.source_id.endswith(a.pmid[-3:])),
            None,
        )
        sid = rubric.source_id if rubric else f"S-{i+1:03d}"
        n = rubric.n_disordered if rubric else "NR"
        lines.append(f"| {sid} | {a.short_author} ({a.year}) | {a.journal[:25]} | {design} | {n} | {rob} |")
    lines.append("")

    # 3.3 Risk of bias summary
    lines += ["### 3.3 Risk of Bias Within Studies *(PRISMA Item 18)*\n"]
    if result.bias_assessment:
        lines.append(result.bias_assessment)
    if result.critical_appraisals:
        concern_counts: dict[str, int] = {}
        for ap in result.critical_appraisals:
            c = ap.overall_concern_score
            concern_counts[c] = concern_counts.get(c, 0) + 1
        total_ap = len(result.critical_appraisals)
        lines += [
            "",
            "**Four-domain appraisal — corpus summary:**\n",
            "| Overall Concern | n | % |",
            "|-----------------|---|---|",
        ]
        for level in ("Low", "Some", "High", "Not Assessed"):
            count = concern_counts.get(level, 0)
            if count:
                lines.append(f"| {level} | {count} | {100*count//total_ap}% |")
    lines.append("")

    # 3.4 Individual study results
    lines += ["### 3.4 Results of Individual Studies *(PRISMA Item 19)*\n"]
    if result.narrative_rows:
        lines += [
            "| Source ID | Design / Sample | Methods | Outcomes | Limitations |",
            "|-----------|----------------|---------|----------|-------------|",
        ]
        for row in result.narrative_rows:
            lines.append(
                f"| {row.source_id} "
                f"| {row.study_design_sample_dataset[:50]} "
                f"| {row.methods[:50]} "
                f"| {row.outcomes[:50]} "
                f"| {row.key_limitations[:50]} |"
            )
    else:
        lines.append("*See Appendix D for per-source charting.*")
    lines.append("")

    # 3.5 Synthesis
    lines += [
        "### 3.5 Synthesis of Results *(PRISMA Item 20)*\n",
        result.synthesis_text or "*Synthesis pending.*",
        "",
    ]

    # 3.6 Certainty of evidence (GRADE)
    if result.grade_assessments:
        lines += [
            "### 3.6 Certainty of Evidence — GRADE *(PRISMA Item 22)*\n",
            "| Outcome | Certainty | Summary |",
            "|---------|-----------|---------|",
        ]
        for outcome, grade in result.grade_assessments.items():
            lines.append(
                f"| {outcome} | **{grade.overall_certainty.value}** | {grade.summary[:80]} |"
            )
        lines.append("")

    lines += ["---", ""]

    # ── §2.6 DISCUSSION ──────────────────────────────────────────────────
    lines += [
        "## 4. Discussion\n",
        "### 4.1 Summary of Evidence\n",
        f"This systematic review identified {f.included_synthesis} studies meeting inclusion criteria "
        f"from a search of {', '.join(p.databases)}. See Section 3.5 for the full synthesis.\n",
        "### 4.2 Strengths and Limitations\n",
        "**Strengths of this review:**",
        "- Comprehensive multi-database search with citation chasing",
        "- Structured per-source charting (Sections A–G) for every included study",
        "- Four-domain critical appraisal applied consistently",
        "- PRISMA 2020 compliant reporting",
        "",
        "**Limitations of the evidence base:**",
    ]
    if result.limitations:
        lines.append(result.limitations)
    else:
        lines += [
            "- Potential publication bias",
            "- Heterogeneity in study designs limits pooled synthesis",
        ]
    lines += [
        "",
        f"**Limitations of the review process:**",
        f"- Search restricted to {', '.join(p.databases)}; other sources not covered",
        "- AI-assisted screening must be verified before publication",
        "",
        "### 4.3 Implications *(EPICOT)*\n",
        f"- **Evidence:** Current certainty is [GRADE level] for primary outcomes",
        f"- **Population:** {p.pico_population or '[population]'} — subgroup data needed",
        f"- **Intervention:** {p.pico_intervention or '[intervention]'} — optimal parameters unclear",
        f"- **Comparison:** {p.pico_comparison or '[comparator]'} — head-to-head trials needed",
        f"- **Outcome:** {p.pico_outcome or '[outcome]'} — measurement standardisation required",
        f"- **Timeframe:** Longitudinal follow-up beyond {p.date_range_start or 'N/A'}–{p.date_range_end or 'present'}",
        "",
    ]

    # 4.4 Grounding validation (if available)
    if result.grounding_validation:
        gv = result.grounding_validation
        lines += [
            "### 4.4 AI Synthesis Grounding Validation\n",
            f"**Verdict:** `{gv.overall_verdict}` | "
            f"Claims supported: {gv.grounding_rate:.1%} | "
            f"Critical errors: {gv.critical_error_count} | "
            f"Hallucinated citations: {gv.hallucinated_citation_count}\n",
            f"{gv.n_atomic_claims} atomic claims analysed. "
            + (
                "All claims are well-grounded in the source corpus."
                if gv.overall_verdict == "PASS"
                else "Some claims require verification against source materials."
            ),
            "",
        ]

    lines += ["---", ""]

    # ── §2.7 CONCLUSIONS ─────────────────────────────────────────────────
    lines += ["## 5. Conclusions\n"]
    if result.conclusions_text:
        lines.append(result.conclusions_text)
    else:
        lines.append(
            f"This systematic review synthesised evidence on: {p.question}. "
            f"{f.included_synthesis} studies were included. "
            "Further high-quality research is needed to address the identified gaps."
        )
    lines += ["", "---", ""]

    # ── §2.8 DECLARATIONS AND END MATTER ─────────────────────────────────
    lines += [
        "## 6. Declarations\n",
        f"- **Protocol registration:** {p.registration_number or 'Not registered'}",
        f"- **Funding:** {p.funding_sources or 'None declared'}",
        f"- **Competing interests:** {p.competing_interests or 'None declared'}",
        f"- **Author contributions (CRediT):** Conceptualization, Methodology, "
        "Investigation, Writing – Original Draft, Writing – Review & Editing.",
        f"- **Data availability:** See Appendix D (charting) and Appendix E (appraisal).",
        f"- **Amendments:** {p.amendments or 'None'}",
        "",
        "---",
        "",
        "## References\n",
        f"*Citation style: {citation_style}. "
        "An asterisk (\\*) marks studies included in the synthesis.*\n",
    ]
    for i, a in enumerate(included, 1):
        lines.append(f"\\*{i}. {a.citation}")
    lines += ["", "---", ""]

    # ── §2.9 APPENDICES ──────────────────────────────────────────────────
    lines += ["## Appendices\n"]

    # Appendix A: PRISMA 2020 Checklist
    lines += [
        "### Appendix A: PRISMA 2020 Checklist\n",
        "| # | Item | Section | Reported |",
        "|---|------|---------|----------|",
        "| 1 | Title identifies as systematic review | Title page | Yes |",
        "| 2 | Structured abstract | Abstract | " + ("Yes" if result.structured_abstract else "Partial") + " |",
        "| 3 | Rationale | Intro §1.2–1.3 | Yes |",
        "| 4 | Objectives / PICO | Intro §1.4 | Yes |",
        "| 5 | Eligibility criteria | Methods §2.2 | Yes |",
        "| 6 | Information sources | Methods §2.3 | Yes |",
        "| 7 | Search strategy | Methods §2.4 + App B | Yes |",
        "| 8 | Selection process | Methods §2.5 | Yes |",
        "| 9 | Data extraction | Methods §2.6 | Yes |",
        "| 10 | Risk-of-bias tools | Methods §2.7 | Yes |",
        "| 11 | Effect measures | Methods §2.8 | Partial |",
        "| 12 | Synthesis methods | Methods §2.8 | Yes |",
        "| 13 | PRISMA flow | Results §3.1 | Yes |",
        "| 14 | Study characteristics | Results §3.2 | Yes |",
        "| 15 | RoB results per study | Results §3.3 | Yes |",
        "| 16 | Individual study results | Results §3.4 | Yes |",
        "| 17 | Synthesis | Results §3.5 | Yes |",
        "| 18 | Reporting bias | Results §3.5 | Partial |",
        "| 19 | GRADE certainty | Results §3.6 | " + ("Yes" if result.grade_assessments else "No") + " |",
        "| 20 | Discussion: summary | Discussion §4.1 | Yes |",
        "| 21 | Limitations | Discussion §4.2 | Yes |",
        "| 22 | Conclusions | §5 | " + ("Yes" if result.conclusions_text else "Partial") + " |",
        "| 23 | Registration | Declarations §6 | Yes |",
        "| 24 | Funding | Declarations §6 | Yes |",
        "| 25 | COI | Declarations §6 | Yes |",
        "| 26 | Excluded studies | Appendix C | Yes |",
        "| 27 | Data availability | Appendix D | Yes |",
        "",
    ]

    # Appendix B: Full search strategies
    lines += ["### Appendix B: Full Search Strategies\n"]
    for i, q in enumerate(result.search_queries or [], 1):
        lines += [f"**Query {i}:**", "```", q, "```", ""]
    lines += [f"*Last search date: {ts}*", ""]

    # Appendix C: Excluded full-text studies
    lines += ["### Appendix C: Excluded Full-Text Studies\n"]
    excluded_ft = [
        s for s in result.screening_log
        if s.decision.value == "exclude" and s.stage.value == "full_text"
    ]
    if excluded_ft:
        lines += ["| Study | Reason |", "|-------|--------|"]
        for s in excluded_ft[:100]:
            lines.append(f"| {s.title[:60]} | {(s.reason or 'Not specified')[:80]} |")
    else:
        lines.append("*No full-text exclusions recorded.*")
    lines.append("")

    # Appendix D: Data charting (per-source)
    lines += [
        "### Appendix D: Data Extraction — Per-Source Charting\n",
        "*Full CSV available as `_charting.csv`. JSON schema per source in `_sources.json`.*\n",
    ]
    for rubric in result.data_charting_rubrics:
        lines += [
            f"#### {rubric.source_id}: {rubric.title[:80]}",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Authors (Year) | {rubric.authors} ({rubric.year}) |",
            f"| Venue | {rubric.journal_conference} |",
            f"| Study Design | {rubric.study_design} |",
            f"| Setting / Country | {rubric.study_setting} / {rubric.country_region} |",
            f"| Primary N | {rubric.n_disordered} |",
            f"| Comparison N | {rubric.n_controls} |",
            f"| Data Types | {rubric.data_types} |",
            f"| Methods Category | {rubric.model_category} |",
            f"| Key Results | {rubric.key_performance_results} |",
            f"| Key Findings | {rubric.summary_key_findings} |",
            "",
        ]

    # Appendix E: Critical appraisal
    lines += [
        "### Appendix E: Risk-of-Bias and Four-Domain Appraisal\n",
        "*Full CSV available as `_appraisal.csv`.*\n",
        "| Source ID | Domain 1 | Domain 2 | Domain 3 | Domain 4 | Overall |",
        "|-----------|----------|----------|----------|----------|---------|",
    ]
    for ap in result.critical_appraisals:
        d1 = ap.domain_1_participant_quality.overall_concern
        d2 = ap.domain_2_data_collection_quality.overall_concern
        d3 = ap.domain_3_feature_model_quality.overall_concern
        d4 = ap.domain_4_bias_transparency.overall_concern
        lines.append(
            f"| {ap.source_id} | {d1} | {d2} | {d3} | {d4} | **{ap.overall_concern_score}** |"
        )
    lines.append("")

    # Appendix F: Additional analyses
    lines += [
        "### Appendix F: Additional Analyses\n",
        "*Subgroup analyses, sensitivity analyses, and meta-regression outputs, if performed.*",
        "",
    ]

    # Appendix G: Protocol
    lines += [
        "### Appendix G: Protocol\n",
        f"- **Registration:** {p.registration_number or 'Not registered'}",
        f"- **Protocol URL:** {p.protocol_url or 'Not available'}",
        f"- **RoB Tool:** {p.rob_tool.value}",
        f"- **Amendments:** {p.amendments or 'None'}",
        "",
    ]

    # Pre-delivery quality checklist (§8 of brief)
    if result.quality_checklist:
        lines += ["### Pre-Delivery Quality Checklist *(brief §8)*\n"]
        for item, passed in result.quality_checklist.items():
            mark = "✅" if passed else "❌"
            lines.append(f"- {mark} {item.replace('_', ' ').title()}")
        lines.append("")

    lines += [
        "---",
        "",
        f"*Generated by PRISMA Review Agent — {result.timestamp}*",
        "*AI-assisted components must be verified before submission.*",
    ]
    return "\n".join(lines)


def _DELETED_to_enhanced_markdown_legacy(result: PRISMAReviewResult) -> str:
    """Legacy broken version — replaced by to_enhanced_markdown above."""
    p = result.protocol
    f = result.flow
    included = result.included_articles

    lines = [
        f"# {p.title or 'Systematic Literature Review Brief'}",
        f"\n<div style='border: 2px solid #2E86AB; padding: 15px; margin: 10px 0; background-color: #F8F9FA;'>",
        f"<strong>Generated:</strong> {result.timestamp} | <strong>PRISMA 2020 Compliant</strong> | <strong>AI-Enhanced Analysis</strong>",
        f"<br><strong>Studies Included:</strong> {f.included_synthesis} | <strong>Total Identified:</strong> {f.total_identified}",
        f"</div>\n",
        "---\n",

        "## Executive Summary\n",
        f"<div style='background-color: #E8F4FD; padding: 15px; border-left: 4px solid #2E86AB; margin: 10px 0;'>",
        f"<h4>🎯 Objective</h4>",
        f"<p>{p.objective}</p>",
        f"<h4>📊 Key Findings</h4>",
        f"<ul>",
        f"<li><strong>{f.included_synthesis}</strong> studies met inclusion criteria</li>",
        f"<li>Systematic search across {', '.join(p.databases)}</li>",
        f"<li>Data charting and critical appraisal completed for all included studies</li>",
        f"<li>Risk of bias assessed using {p.rob_tool.value}</li>",
        f"</ul>",
        f"</div>\n",

        "---\n",
        "## 1. Background and Rationale\n",
        f"### 1.1 Research Context\n",
        f"{p.objective}\n",
        f"### 1.2 Research Questions\n",
        f"<div style='background-color: #FFF3CD; padding: 10px; border: 1px solid #FFEAA7; margin: 10px 0;'>",
        f"<strong>PICO Framework:</strong>",
        f"<ul>",
        f"<li><strong>Population:</strong> {p.pico_population or 'Not specified'}</li>",
        f"<li><strong>Intervention:</strong> {p.pico_intervention or 'Not specified'}</li>",
        f"<li><strong>Comparison:</strong> {p.pico_comparison or 'Not specified'}</li>",
        f"<li><strong>Outcome:</strong> {p.pico_outcome or 'Not specified'}</li>",
        f"</ul>",
        f"</div>\n",

        "---\n",
        "## 2. Methods\n",
        f"### 2.1 Study Design\n",
        f"This systematic review follows the **PRISMA 2020** guidelines for reporting systematic reviews.\n",
        f"### 2.2 Eligibility Criteria\n",
        f"<table style='border-collapse: collapse; width: 100%; margin: 10px 0;'>",
        f"<tr style='background-color: #2E86AB; color: white;'>",
        f"<th style='border: 1px solid #ddd; padding: 8px;'>Criterion</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px;'>Inclusion</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px;'>Exclusion</th>",
        f"</tr>",
        f"<tr>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Population</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>{p.pico_population or 'N/A'}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Not applicable</td>",
        f"</tr>",
        f"<tr style='background-color: #f9f9f9;'>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Intervention</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>{p.pico_intervention or 'N/A'}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Not applicable</td>",
        f"</tr>",
        f"<tr>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Study Design</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>{p.inclusion_criteria}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>{p.exclusion_criteria}</td>",
        f"</tr>",
        f"</table>\n",

        f"### 2.3 Information Sources and Search Strategy\n",
        f"<div style='display: flex; gap: 20px; margin: 15px 0;'>",
        f"<div style='flex: 1;'>",
        f"<h4>📚 Databases Searched</h4>",
        f"<ul>" + "".join([f"<li>{db}</li>" for db in p.databases]) + f"</ul>",
        f"</div>",
        f"<div style='flex: 1;'>",
        f"<h4>🔍 Search Strategy</h4>",
        f"<p><strong>Date Range:</strong> {p.date_range_start or 'N/A'} to {p.date_range_end or 'Present'}</p>",
        f"<p><strong>Citation Depth:</strong> Up to {p.max_hops} hops</p>",
        f"<p><strong>Last Search:</strong> {result.timestamp[:10]}</p>",
        f"</div>",
        f"</div>\n",

        f"#### Search Queries Executed\n",
        f"<div style='background-color: #F8F9FA; padding: 10px; border: 1px solid #DEE2E6; margin: 10px 0; font-family: monospace;'>",
    ]

    for i, q in enumerate(result.search_queries, 1):
        lines.append(f"{i}. `{q}`")

    lines.extend([
        f"</div>\n",
        f"### 2.4 Study Selection Process\n",
        f"<div style='background-color: #D1ECF1; padding: 15px; border: 1px solid #BEE5EB; margin: 10px 0;'>",
        f"<h4>🔬 Screening Methodology</h4>",
        f"<p>Studies were screened using a two-stage process:</p>",
        f"<ol>",
        f"<li><strong>Title/Abstract Screening:</strong> AI-assisted batch screening with human verification</li>",
        f"<li><strong>Full-text Screening:</strong> Detailed eligibility assessment against inclusion/exclusion criteria</li>",
        f"</ol>",
        f"<p><strong>Screening Tools:</strong> Automated relevance scoring, keyword matching, and manual verification</p>",
        f"</div>\n",

        f"### 2.5 Data Extraction and Synthesis\n",
        f"<div style='background-color: #D4EDDA; padding: 15px; border: 1px solid #C3E6CB; margin: 10px 0;'>",
        f"<h4>📋 Data Charting Rubric</h4>",
        f"<p>All included studies underwent structured data extraction using a comprehensive 7-section rubric:</p>",
        f"<ul>",
        f"<li><strong>Section A:</strong> Study identification and general information</li>",
        f"<li><strong>Section B:</strong> Study design and methodology</li>",
        f"<li><strong>Section C:</strong> Participant characteristics</li>",
        f"<li><strong>Section D:</strong> Intervention/exposure details</li>",
        f"<li><strong>Section E:</strong> Outcomes and measurements</li>",
        f"<li><strong>Section F:</strong> Results and key findings</li>",
        f"<li><strong>Section G:</strong> Study quality and limitations</li>",
        f"</ul>",
        f"</div>\n",

        f"### 2.6 Risk of Bias Assessment\n",
        f"Risk of bias was assessed using **{p.rob_tool.value}** across relevant domains for each study design.\n",

        f"### 2.7 Critical Appraisal\n",
        f"Critical appraisal was performed across four domains:\n",
        f"<ol>",
        f"<li><strong>Participant Quality:</strong> Representativeness and selection bias</li>",
        f"<li><strong>Data Collection:</strong> Measurement validity and reliability</li>",
        f"<li><strong>Feature/Model Quality:</strong> Analytical approach and statistical methods</li>",
        f"<li><strong>Bias and Transparency:</strong> Reporting quality and potential conflicts</li>",
        f"</ol>\n",

        "---\n",
        "## 3. Results\n",
        f"### 3.1 Study Selection Flow\n",
        f"<div style='text-align: center; margin: 20px 0;'>",
        f"![PRISMA Flow Diagram](prisma_flow_diagram.png)",
        f"<br><em>Figure 1: PRISMA 2020 Flow Diagram</em>",
        f"</div>\n",

        f"#### PRISMA Flow Summary\n",
        f"<table style='border-collapse: collapse; width: 100%; margin: 15px 0;'>",
        f"<thead>",
        f"<tr style='background-color: #2E86AB; color: white;'>",
        f"<th style='border: 1px solid #ddd; padding: 12px; text-align: left;'>Stage</th>",
        f"<th style='border: 1px solid #ddd; padding: 12px; text-align: center;'>Count</th>",
        f"<th style='border: 1px solid #ddd; padding: 12px; text-align: left;'>Description</th>",
        f"</tr>",
        f"</thead>",
        f"<tbody>",
        f"<tr>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>PubMed</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.db_pubmed}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Primary database search results</td>",
        f"</tr>",
        f"<tr style='background-color: #f9f9f9;'>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>bioRxiv</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.db_biorxiv}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Preprint server results</td>",
        f"</tr>",
        f"<tr>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Related Articles</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.db_related}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>PubMed related articles expansion</td>",
        f"</tr>",
        f"<tr style='background-color: #f9f9f9;'>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Citation Hops</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.db_hops}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Forward/backward citation navigation</td>",
        f"</tr>",
        f"<tr style='background-color: #E8F4FD;'>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Total Identified</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center; font-weight: bold;'>{f.total_identified}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Total records identified</td>",
        f"</tr>",
        f"<tr>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Duplicates Removed</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.duplicates_removed}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Duplicate records excluded</td>",
        f"</tr>",
        f"<tr style='background-color: #f9f9f9;'>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Screened</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.screened_title_abstract}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Title/abstract screening</td>",
        f"</tr>",
        f"<tr>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Excluded (Screening)</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.excluded_title_abstract}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Excluded during screening</td>",
        f"</tr>",
        f"<tr style='background-color: #f9f9f9;'>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Full-text Assessed</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.assessed_eligibility}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Full-text eligibility assessment</td>",
        f"</tr>",
        f"<tr>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Excluded (Eligibility)</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{f.excluded_eligibility}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'>Excluded during eligibility assessment</td>",
        f"</tr>",
        f"<tr style='background-color: #D4EDDA;'>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Included</strong></td>",
        f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center; font-weight: bold;'>{f.included_synthesis}</td>",
        f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Studies included in synthesis</strong></td>",
        f"</tr>",
        f"</tbody>",
        f"</table>\n",
    ])

    # Study characteristics
    lines.extend([
        f"### 3.2 Study Characteristics\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; border: 1px solid #DEE2E6; margin: 15px 0;'>",
        f"<h4>📊 Study Overview</h4>",
        f"<p><strong>Total Studies Included:</strong> {len(included)}</p>",
        f"<p><strong>Publication Years:</strong> {min([a.year for a in included] + [9999]) if included else 'N/A'} - {max([a.year for a in included] + [0]) if included else 'N/A'}</p>",
        f"</div>\n",

        f"#### Included Studies Summary\n",
        f"<table style='border-collapse: collapse; width: 100%; margin: 15px 0;'>",
        f"<thead>",
        f"<tr style='background-color: #2E86AB; color: white;'>",
        f"<th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Authors</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px; text-align: center;'>Year</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Journal</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px; text-align: center;'>Design</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px; text-align: center;'>RoB</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px; text-align: center;'>Charted</th>",
        f"</tr>",
        f"</thead>",
        f"<tbody>"
    ])

    for i, a in enumerate(included):
        design = a.extracted_data.study_design if a.extracted_data else "NR"
        rob = a.risk_of_bias.overall.value if a.risk_of_bias else "NR"
        charted = "✅" if any(r.source_id.endswith(str(a.pmid)[-3:]) for r in result.data_charting_rubrics) else "❌"
        bg_color = "#f9f9f9" if i % 2 == 0 else "white"
        lines.append(
            f"<tr style='background-color: {bg_color};'>"
            f"<td style='border: 1px solid #ddd; padding: 8px;'>{a.short_author}</td>"
            f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{a.year}</td>"
            f"<td style='border: 1px solid #ddd; padding: 8px;'>{a.journal[:30]}</td>"
            f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{design}</td>"
            f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{rob}</td>"
            f"<td style='border: 1px solid #ddd; padding: 8px; text-align: center;'>{charted}</td>"
            f"</tr>"
        )

    lines.append("</tbody></table>\n")

    # Data Charting Results
    if result.data_charting_rubrics:
        lines.extend([
            f"### 3.3 Data Charting Results\n",
            f"<div style='background-color: #E8F4FD; padding: 15px; border: 1px solid #BEE5EB; margin: 15px 0;'>",
            f"<h4>📋 Data Charting Summary</h4>",
            f"<p><strong>Studies Successfully Charted:</strong> {len(result.data_charting_rubrics)}</p>",
        ])

        # Disorder distribution
        disorder_counts = {}
        for r in result.data_charting_rubrics:
            disorder_counts[r.disorder_cohort] = disorder_counts.get(r.disorder_cohort, 0) + 1

        if disorder_counts:
            lines.append(f"<h5>Disorder Cohorts Represented:</h5>")
            lines.append(f"<ul>")
            for cohort, count in sorted(disorder_counts.items()):
                if cohort and cohort != "Not Reported":
                    lines.append(f"<li><strong>{cohort}:</strong> {count} studies</li>")
            lines.append(f"</ul>")

        lines.append(f"</div>\n")

        # Study design distribution
        design_counts = {}
        for r in result.data_charting_rubrics:
            design_counts[r.study_design] = design_counts.get(r.study_design, 0) + 1

        lines.extend([
            f"#### Study Design Distribution\n",
            f"<div style='display: flex; gap: 20px; margin: 15px 0;'>",
        ])

        for design, count in sorted(design_counts.items()):
            if design and design != "Not Reported":
                percentage = (count / len(result.data_charting_rubrics)) * 100
                lines.append(
                    f"<div style='flex: 1; text-align: center;'>"
                    f"<div style='background-color: #2E86AB; color: white; padding: 10px; margin-bottom: 5px;'>{design}</div>"
                    f"<div style='font-size: 24px; font-weight: bold;'>{count}</div>"
                    f"<div style='color: #666;'>{percentage:.1f}%</div>"
                    f"</div>"
                )

        lines.append(f"</div>\n")

    # Critical Appraisal Results
    if result.critical_appraisals:
        lines.extend([
            f"### 3.4 Critical Appraisal Results\n",
            f"<div style='background-color: #FFF3CD; padding: 15px; border: 1px solid #FFEAA7; margin: 15px 0;'>",
            f"<h4>🔍 Critical Appraisal Summary</h4>",
        ])

        concern_counts = {"Low": 0, "Some": 0, "High": 0, "Not Assessed": 0}
        for appraisal in result.critical_appraisals:
            concern_counts[appraisal.overall_concern_score] += 1

        lines.append(f"<h5>Overall Concern Distribution:</h5>")
        lines.append(f"<div style='display: flex; gap: 10px; margin: 10px 0;'>")

        colors = {"Low": "#28A745", "Some": "#FFC107", "High": "#DC3545", "Not Assessed": "#6C757D"}
        for level, count in concern_counts.items():
            if count > 0:
                percentage = (count / len(result.critical_appraisals)) * 100
                lines.append(
                    f"<div style='flex: 1; text-align: center;'>"
                    f"<div style='background-color: {colors[level]}; color: white; padding: 8px; margin-bottom: 5px; border-radius: 4px;'>{level}</div>"
                    f"<div style='font-size: 20px; font-weight: bold;'>{count}</div>"
                    f"<div style='color: #666; font-size: 12px;'>{percentage:.1f}%</div>"
                    f"</div>"
                )

        lines.extend([
            f"</div>",
            f"</div>\n",
        ])

    # Synthesis
    lines.extend([
        f"### 3.5 Synthesis of Results\n",
        f"<div style='background-color: #F8F9FA; padding: 20px; border: 1px solid #DEE2E6; margin: 15px 0;'>",
        f"<h4>📝 Synthesis</h4>",
        f"{result.synthesis_text or '[Synthesis pending - AI-generated summary of key findings across all included studies]'}",
        f"</div>\n",
    ])

    # Risk of bias summary
    if result.bias_assessment:
        lines.extend([
            f"### 3.6 Risk of Bias Assessment\n",
            f"<div style='background-color: #F8D7DA; padding: 15px; border: 1px solid #F5C6CB; margin: 15px 0;'>",
            f"<h4>⚠️ Risk of Bias Summary</h4>",
            f"{result.bias_assessment}",
            f"</div>\n",
        ])

    # GRADE assessment
    if result.grade_assessments:
        lines.extend([
            f"### 3.7 Certainty of Evidence (GRADE)\n",
            f"<div style='background-color: #D1ECF1; padding: 15px; border: 1px solid #BEE5EB; margin: 15px 0;'>",
            f"<h4>📊 GRADE Assessment</h4>",
        ])

        for outcome, grade in result.grade_assessments.items():
            certainty_color = {
                "High": "#28A745",
                "Moderate": "#FFC107",
                "Low": "#FD7E14",
                "Very Low": "#DC3545"
            }.get(grade.overall_certainty.value, "#6C757D")

            lines.extend([
                f"<div style='margin: 10px 0; padding: 10px; border: 1px solid #ddd; border-radius: 4px;'>",
                f"<strong>{outcome}:</strong> "
                f"<span style='background-color: {certainty_color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px;'>"
                f"{grade.overall_certainty.value}</span>",
                f"<br><em>{grade.summary}</em>",
                f"</div>",
            ])

        lines.append(f"</div>\n")

    # Discussion
    lines.extend([
        "---\n",
        "## 4. Discussion\n",
        f"### 4.1 Summary of Evidence\n",
        f"<div style='background-color: #E8F4FD; padding: 15px; border: 1px solid #BEE5EB; margin: 15px 0;'>",
        f"<p>This systematic review identified {f.included_synthesis} studies that met the inclusion criteria. "
        f"The evidence base demonstrates [key findings to be summarized based on synthesis].</p>",
        f"</div>\n",

        f"### 4.2 Strengths and Limitations\n",
        f"<div style='display: flex; gap: 20px; margin: 15px 0;'>",
        f"<div style='flex: 1; background-color: #D4EDDA; padding: 15px; border: 1px solid #C3E6CB;'>",
        f"<h4>✅ Strengths</h4>",
        f"<ul>",
        f"<li>Comprehensive search strategy across multiple databases</li>",
        f"<li>Structured data charting for all included studies</li>",
        f"<li>Critical appraisal of study quality</li>",
        f"<li>PRISMA 2020 compliant reporting</li>",
        f"</ul>",
        f"</div>",
        f"<div style='flex: 1; background-color: #F8D7DA; padding: 15px; border: 1px solid #F5C6CB;'>",
        f"<h4>⚠️ Limitations</h4>",
    ])

    if result.limitations:
        lines.append(f"{result.limitations}")
    else:
        lines.extend([
            f"<ul>",
            f"<li>Potential publication bias</li>",
            f"<li>Heterogeneity across study designs</li>",
            f"<li>Limited generalizability</li>",
            f"</ul>",
        ])

    lines.extend([
        f"</div>",
        f"</div>\n",

        f"### 4.3 Grounding Validation\n",
    ])

    if result.grounding_validation:
        gv = result.grounding_validation
        verdict_color = {
            "PASS": "#28A745",
            "REVISE": "#FFC107",
            "FAIL": "#DC3545"
        }.get(gv.overall_verdict, "#6C757D")

        lines.extend([
            f"<div style='background-color: #FFF3CD; padding: 15px; border: 1px solid #FFEAA7; margin: 15px 0;'>",
            f"<h4>🔍 AI Synthesis Grounding Validation</h4>",
            f"<div style='display: flex; gap: 20px; margin: 10px 0;'>",
            f"<div style='flex: 1; text-align: center;'>",
            f"<div style='font-size: 24px; font-weight: bold; color: {verdict_color};'>{gv.overall_verdict}</div>",
            f"<div style='color: #666;'>Overall Verdict</div>",
            f"</div>",
            f"<div style='flex: 1; text-align: center;'>",
            f"<div style='font-size: 24px; font-weight: bold;'>{gv.grounding_rate:.1%}</div>",
            f"<div style='color: #666;'>Claims Supported</div>",
            f"</div>",
            f"<div style='flex: 1; text-align: center;'>",
            f"<div style='font-size: 24px; font-weight: bold;'>{gv.critical_error_count}</div>",
            f"<div style='color: #666;'>Critical Errors</div>",
            f"</div>",
            f"</div>",
            f"<p><strong>Validation Summary:</strong> {gv.n_atomic_claims} atomic claims analyzed. "
            f"{'All claims are well-grounded in source materials.' if gv.overall_verdict == 'PASS' else 'Some claims require verification against source materials.'}</p>",
        ])

        if gv.claims:
            lines.extend([
                f"<h5>Detailed Claim Validation:</h5>",
                f"<table style='width: 100%; border-collapse: collapse; margin: 10px 0; font-size: 12px;'>",
                f"<thead>",
                f"<tr style='background-color: #2E86AB; color: white;'>",
                f"<th style='border: 1px solid #ddd; padding: 6px;'>Claim</th>",
                f"<th style='border: 1px solid #ddd; padding: 6px;'>Type</th>",
                f"<th style='border: 1px solid #ddd; padding: 6px;'>Verdict</th>",
                f"<th style='border: 1px solid #ddd; padding: 6px;'>Source</th>",
                f"</tr>",
                f"</thead>",
                f"<tbody>",
            ])

            for claim in gv.claims[:10]:  # Limit to first 10 claims for readability
                verdict_color = {
                    "SUPPORTED": "#28A745",
                    "PARTIALLY_SUPPORTED": "#FFC107",
                    "UNSUPPORTED": "#DC3545",
                    "CONTRADICTED": "#DC3545",
                    "UNVERIFIABLE": "#6C757D"
                }.get(claim.verdict.value, "#6C757D")

                lines.append(
                    f"<tr>"
                    f"<td style='border: 1px solid #ddd; padding: 6px;'>{claim.excerpt_text[:50]}...</td>"
                    f"<td style='border: 1px solid #ddd; padding: 6px;'>{claim.claim_type.value[:10]}</td>"
                    f"<td style='border: 1px solid #ddd; padding: 6px; text-align: center;'>"
                    f"<span style='background-color: {verdict_color}; color: white; padding: 2px 6px; border-radius: 8px; font-size: 10px;'>{claim.verdict.value[:12]}</span>"
                    f"</td>"
                    f"<td style='border: 1px solid #ddd; padding: 6px;'>{', '.join(claim.cited_sources[:2])}</td>"
                    f"</tr>"
                )

            lines.extend([
                f"</tbody>",
                f"</table>",
            ])

        lines.append(f"</div>\n")
    else:
        lines.extend([
            f"<div style='background-color: #F8F9FA; padding: 15px; border: 1px solid #DEE2E6; margin: 15px 0;'>",
            f"<p><em>Grounding validation was not performed for this synthesis.</em></p>",
            f"</div>\n",
        ])

    lines.extend([
        f"### 4.4 Implications for Practice\n",
        f"<div style='background-color: #FFF3CD; padding: 15px; border: 1px solid #FFEAA7; margin: 15px 0;'>",
        f"<h4>🏥 Practice Implications</h4>",
        f"<p>[AI-generated implications for clinical practice, policy, or research based on the synthesis]</p>",
        f"</div>\n",

        f"### 4.5 Implications for Research\n",
        f"<div style='background-color: #E8F4FD; padding: 15px; border: 1px solid #BEE5EB; margin: 15px 0;'>",
        f"<h4>🔬 Research Implications</h4>",
        f"<p>[AI-generated recommendations for future research directions]</p>",
        f"</div>\n",

        "---\n",
        "## 5. Conclusions\n",
        f"<div style='background-color: #2E86AB; color: white; padding: 20px; margin: 15px 0; border-radius: 8px;'>",
        f"<h3 style='margin-top: 0;'>📋 Conclusions</h3>",
        f"<p>This systematic review provides comprehensive evidence on [topic]. "
        f"The findings suggest [main conclusions]. "
        f"Further research is needed to [research gaps identified].</p>",
        f"<p><strong>Key Takeaways:</strong></p>",
        f"<ul>",
        f"<li>[Key finding 1]</li>",
        f"<li>[Key finding 2]</li>",
        f"<li>[Key finding 3]</li>",
        f"</ul>",
        f"</div>\n",

        "---\n",
        "## 6. References\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0;'>",
        f"<ol>",
    ])

    for i, a in enumerate(included, 1):
        lines.append(f"<li style='margin-bottom: 8px;'>{a.citation}</li>")

    lines.extend([
        f"</ol>",
        f"</div>\n",

        "---\n",
        "## Appendices\n",

        f"### Appendix A: PRISMA Flowchart\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0;'>",
        f"<h4>📊 Study Selection Flowchart</h4>",
        f"<p><em>[Figure Placeholder: PRISMA Flowchart showing study selection process]</em></p>",
        f"<pre style='background-color: white; padding: 15px; border: 1px solid #DEE2E6; font-family: monospace;'>",
        f"Records identified (n={f.total_identified})",
        f"│",
        f"├── Records after duplicates removed (n={f.after_dedup})",
        f"│",
        f"├── Records screened (n={f.screened_title_abstract})",
        f"│",
        f"├── Records excluded (n={f.excluded_title_abstract})",
        f"│",
        f"└── Full-text articles assessed for eligibility (n={f.assessed_eligibility})",
        f"    │",
        f"    ├── Full-text articles excluded (n={f.excluded_eligibility})",
        f"    │   └── Reason: [see Appendix C]",
        f"    │",
        f"    └── Studies included in review (n={f.included_synthesis})",
        f"</pre>",
        f"</div>\n",

        f"### Appendix B: Search Strategy\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0;'>",
        f"<h4>🔍 Complete Search Strategy</h4>",
        f"<p><strong>Databases:</strong> {', '.join(p.databases)}</p>",
        f"<p><strong>Search Date:</strong> {result.timestamp[:10]}</p>",
        f"<p><strong>Search Terms:</strong></p>",
        f"<pre style='background-color: white; padding: 15px; border: 1px solid #DEE2E6;'>" + "\n".join(result.search_queries[:3]) + "</pre>",
        f"</div>\n",

        f"### Appendix C: Risk of Bias Assessment\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0;'>",
        f"<h4>⚖️ Quality Assessment Results</h4>",
        f"<p><em>[Figure Placeholder: Risk of bias summary and graph]</em></p>",
        f"<table style='width: 100%; border-collapse: collapse; margin: 10px 0;'>",
        f"<thead>",
        f"<tr style='background-color: #2E86AB; color: white;'>",
        f"<th style='border: 1px solid #ddd; padding: 8px;'>Study</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px;'>Risk of Bias</th>",
        f"<th style='border: 1px solid #ddd; padding: 8px;'>Overall Quality</th>",
        f"</tr>",
        f"</thead>",
        f"<tbody>",
    ])

    for a in included[:5]:
        rob = a.risk_of_bias.overall.value if a.risk_of_bias else "NR"
        lines.append(
            f"<tr>"
            f"<td style='border: 1px solid #ddd; padding: 8px;'>{a.title[:50]}...</td>"
            f"<td style='border: 1px solid #ddd; padding: 8px;'>{rob}</td>"
            f"<td style='border: 1px solid #ddd; padding: 8px;'>See Appendix E</td>"
            f"</tr>"
        )

    lines.extend([
        f"</tbody>",
        f"</table>",
        f"</div>\n",

        f"### Appendix D: Data Extraction Form\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0;'>",
        f"<h4>📋 Data Extraction Template</h4>",
        f"<p>Standardized data extraction was performed using the following form:</p>",
        f"<ul>",
        f"<li><strong>Study Characteristics:</strong> Author, year, country, study design</li>",
        f"<li><strong>Participants:</strong> Population, sample size, inclusion/exclusion criteria</li>",
        f"<li><strong>Intervention:</strong> Description, dosage, duration</li>",
        f"<li><strong>Outcomes:</strong> Primary and secondary outcomes measured</li>",
        f"<li><strong>Results:</strong> Effect sizes, confidence intervals, p-values</li>",
        f"</ul>",
        f"</div>\n",

        f"### Appendix E: Additional Figures\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0;'>",
        f"<h4>📈 Supplementary Figures</h4>",
        f"<p><em>[Figure Placeholder: Forest plot of effect sizes]</em></p>",
        f"<p><em>[Figure Placeholder: Funnel plot for publication bias assessment]</em></p>",
        f"<p><em>[Figure Placeholder: Subgroup analysis results]</em></p>",
        f"</div>\n",

        f"### Appendix F: Funding and Conflicts of Interest\n",
        f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0;'>",
        f"<h4>💰 Funding Information</h4>",
        f"<p>This systematic review was conducted independently without external funding.</p>",
        f"<p><strong>Conflicts of Interest:</strong> None declared.</p>",
        f"</div>\n",

        "---\n",
        f"<div style='text-align: center; margin: 30px 0; padding: 20px; background-color: #2E86AB; color: white; border-radius: 8px;'>",
        f"<h3 style='margin: 0;'>Systematic Review Completed</h3>",
        f"<p style='margin: 10px 0 0 0;'>Generated by AI-Powered PRISMA Review Agent</p>",
        f"<p style='margin: 5px 0 0 0; font-size: 12px;'>Date: {datetime.now().strftime('%Y-%m-%d')}</p>",
        f"</div>\n",
    ])

    # Appendix A: Data Charting Rubrics
    if result.data_charting_rubrics:
        lines.extend([
            f"### Appendix A: Data Charting Rubrics\n",
            f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0; border: 1px solid #DEE2E6;'>",
            f"<h4>📋 Complete Data Charting Results</h4>",
        ])

        for i, rubric in enumerate(result.data_charting_rubrics, 1):
            lines.extend([
                f"<div style='border: 1px solid #ddd; padding: 15px; margin: 10px 0; background-color: white;'>",
                f"<h5>{i}. {rubric.source_id}: {rubric.title}</h5>",
                f"<table style='width: 100%; border-collapse: collapse; margin: 10px 0;'>",
                f"<tr><td style='border: 1px solid #ddd; padding: 8px; background-color: #f9f9f9; width: 150px;'><strong>Authors</strong></td><td style='border: 1px solid #ddd; padding: 8px;'>{rubric.authors} ({rubric.year})</td></tr>",
                f"<tr><td style='border: 1px solid #ddd; padding: 8px; background-color: #f9f9f9;'><strong>Study Design</strong></td><td style='border: 1px solid #ddd; padding: 8px;'>{rubric.study_design}</td></tr>",
                f"<tr><td style='border: 1px solid #ddd; padding: 8px; background-color: #f9f9f9;'><strong>Participants</strong></td><td style='border: 1px solid #ddd; padding: 8px;'>{rubric.n_disordered} disordered" + (f", {rubric.n_controls} controls" if rubric.n_controls and rubric.n_controls != "Not Reported" else "") + "</td></tr>",
                f"<tr><td style='border: 1px solid #ddd; padding: 8px; background-color: #f9f9f9;'><strong>Key Findings</strong></td><td style='border: 1px solid #ddd; padding: 8px;'>{rubric.summary_key_findings}</td></tr>",
                f"</table>",
                f"</div>",
            ])

        lines.append(f"</div>\n")

    # Appendix B: Critical Appraisal Details
    if result.critical_appraisals:
        lines.extend([
            f"### Appendix B: Critical Appraisal Details\n",
            f"<div style='background-color: #FFF3CD; padding: 15px; margin: 15px 0; border: 1px solid #FFEAA7;'>",
            f"<h4>🔍 Detailed Critical Appraisal Results</h4>",
        ])

        for i, appraisal in enumerate(result.critical_appraisals, 1):
            concern_color = {
                "Low": "#28A745",
                "Some": "#FFC107",
                "High": "#DC3545",
                "Not Assessed": "#6C757D"
            }.get(appraisal.overall_concern_score, "#6C757D")

            lines.extend([
                f"<div style='border: 1px solid #ddd; padding: 15px; margin: 10px 0; background-color: white;'>",
                f"<h5>{i}. {appraisal.source_id}</h5>",
                f"<p><strong>Overall Concern:</strong> "
                f"<span style='background-color: {concern_color}; color: white; padding: 2px 8px; border-radius: 12px; font-size: 12px;'>"
                f"{appraisal.overall_concern_score}</span></p>",
                f"<table style='width: 100%; border-collapse: collapse; margin: 10px 0;'>",
                f"<tr style='background-color: #f9f9f9;'>",
                f"<td style='border: 1px solid #ddd; padding: 8px; width: 200px;'><strong>Domain</strong></td>",
                f"<td style='border: 1px solid #ddd; padding: 8px;'><strong>Assessment</strong></td>",
                f"</tr>",
                f"<tr><td style='border: 1px solid #ddd; padding: 8px;'>Participant Quality</td><td style='border: 1px solid #ddd; padding: 8px;'>{appraisal.domain_1_participant_quality.overall_concern}</td></tr>",
                f"<tr style='background-color: #f9f9f9;'><td style='border: 1px solid #ddd; padding: 8px;'>Data Collection</td><td style='border: 1px solid #ddd; padding: 8px;'>{appraisal.domain_2_data_collection_quality.overall_concern}</td></tr>",
                f"<tr><td style='border: 1px solid #ddd; padding: 8px;'>Feature/Model Quality</td><td style='border: 1px solid #ddd; padding: 8px;'>{appraisal.domain_3_feature_model_quality.overall_concern}</td></tr>",
                f"<tr style='background-color: #f9f9f9;'><td style='border: 1px solid #ddd; padding: 8px;'>Bias & Transparency</td><td style='border: 1px solid #ddd; padding: 8px;'>{appraisal.domain_4_bias_transparency.overall_concern}</td></tr>",
                f"</table>",
                f"</div>",
            ])

        lines.append(f"</div>\n")

    # Appendix C: PRISMA Narrative Rows
    if result.narrative_rows:
        lines.extend([
            f"### Appendix C: PRISMA Narrative Rows\n",
            f"<div style='background-color: #E8F4FD; padding: 15px; margin: 15px 0; border: 1px solid #BEE5EB;'>",
            f"<h4>📝 PRISMA Narrative Summary</h4>",
            f"<table style='width: 100%; border-collapse: collapse; margin: 10px 0;'>",
            f"<thead>",
            f"<tr style='background-color: #2E86AB; color: white;'>",
            f"<th style='border: 1px solid #ddd; padding: 8px;'>Source ID</th>",
            f"<th style='border: 1px solid #ddd; padding: 8px;'>Study Design/Sample/Dataset</th>",
            f"<th style='border: 1px solid #ddd; padding: 8px;'>Methods</th>",
            f"<th style='border: 1px solid #ddd; padding: 8px;'>Outcomes</th>",
            f"<th style='border: 1px solid #ddd; padding: 8px;'>Key Limitations</th>",
            f"<th style='border: 1px solid #ddd; padding: 8px;'>Relevance</th>",
            f"</tr>",
            f"</thead>",
            f"<tbody>",
        ])

        for i, row in enumerate(result.narrative_rows):
            bg_color = "#f9f9f9" if i % 2 == 0 else "white"
            lines.append(
                f"<tr style='background-color: {bg_color};'>"
                f"<td style='border: 1px solid #ddd; padding: 8px;'>{row.source_id}</td>"
                f"<td style='border: 1px solid #ddd; padding: 8px;'>{row.study_design_sample_dataset[:50]}...</td>"
                f"<td style='border: 1px solid #ddd; padding: 8px;'>{row.methods[:50]}...</td>"
                f"<td style='border: 1px solid #ddd; padding: 8px;'>{row.outcomes[:50]}...</td>"
                f"<td style='border: 1px solid #ddd; padding: 8px;'>{row.key_limitations[:50]}...</td>"
                f"<td style='border: 1px solid #ddd; padding: 8px;'>{row.relevance_notes[:50]}...</td>"
                f"</tr>"
            )

        lines.extend([
            f"</tbody>",
            f"</table>",
            f"</div>\n",
        ])

    # Evidence spans
    if result.evidence_spans:
        lines.extend([
            f"### Appendix D: Evidence Spans\n",
            f"<div style='background-color: #F8F9FA; padding: 15px; margin: 15px 0; border: 1px solid #DEE2E6;'>",
            f"<h4>🔍 Supporting Evidence Excerpts</h4>",
        ])

        for e in result.evidence_spans[:20]:
            lines.extend([
                f"<div style='border: 1px solid #ddd; padding: 10px; margin: 8px 0; background-color: white;'>",
                f"<strong>PMID:{e.paper_pmid}</strong> (relevance: {e.relevance_score:.2f})",
                f"<br><em>\"{e.text[:200]}...\"</em>",
                f"</div>",
            ])

        lines.append(f"</div>\n")

    # Footer
    lines.extend([
        "---\n",
        f"<div style='text-align: center; margin: 20px 0; padding: 15px; background-color: #2E86AB; color: white; border-radius: 8px;'>",
        f"<h4 style='margin: 0;'>🤖 Generated by PRISMA Review Agent</h4>",
        f"<p style='margin: 5px 0;'>Timestamp: {result.timestamp}</p>",
        f"<p style='margin: 5px 0; font-size: 12px;'>AI-assisted analysis should be verified before publication</p>",
        f"</div>",
    ])

    return "\n".join(lines)
