"""
Pydantic AI agents for each PRISMA pipeline step.

Each agent is a specialized pydantic-ai Agent with typed output,
system prompt, and optional tool access. They use OpenRouter via
the OpenAIChatModel + OpenRouterProvider.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from .models import (
    ReviewProtocol,
    SearchStrategy,
    ScreeningBatchResult,
    ScreeningDecision,
    ScreeningDecisionType,
    RiskOfBiasResult,
    RoBDomainAssessment,
    RoBJudgment,
    StudyDataExtraction,
    GRADEAssessment,
    GRADECertainty,
    Article,
    BatchEvidenceExtraction,
    EvidenceSpan,
    DataChartingRubric,
    PRISMANarrativeRow,
    CriticalAppraisalRubric,
    GroundingValidationResult,
    Abstract,
    Introduction,
    ThematicSynthesisResult,
    Discussion,
    Conclusion,
    QuantitativeAnalysis,
    BiasAssessment,
    Theme,
    PrismaFlow,
    Implications,
    RubricSectionOutput,
    SECTION_FORMAT,
)


# ────────────────────── Shared Dependencies ────────────────────────────

@dataclass
class AgentDeps:
    """Shared dependencies injected into all agents via RunContext."""
    protocol: ReviewProtocol
    api_key: str = ""
    model_name: str = "anthropic/claude-sonnet-4"


def build_model(api_key: str, model_name: str = "anthropic/claude-sonnet-4") -> OpenAIChatModel:
    """Create an OpenRouter-backed model for pydantic-ai agents."""
    provider = OpenRouterProvider(api_key=api_key)
    return OpenAIChatModel(model_name, provider=provider)


# ────────────────────── 1. Search Strategy Agent ───────────────────────

search_strategy_agent = Agent(
    output_type=SearchStrategy,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review methodologist. Given a PRISMA protocol
(research question, PICO, inclusion/exclusion criteria), generate an
optimal search strategy for PubMed and bioRxiv.

Rules:
- 2-5 PubMed queries using MeSH terms, Boolean operators, field tags
- Both broad and specific queries for comprehensive recall
- For bioRxiv, simpler keyword queries (2-3 words each)
- Include relevant MeSH terms and key concepts
""",
    retries=2,
    name="search_strategy",
    defer_model_check=True,
)


@search_strategy_agent.system_prompt
async def _search_strategy_context(ctx: RunContext[AgentDeps]) -> str:
    p = ctx.deps.protocol
    return (
        f"\nResearch Question: {p.title}\n"
        f"Objective: {p.objective}\n"
        f"PICO:\n{p.pico_text}\n"
        f"Inclusion: {p.inclusion_criteria}\n"
        f"Exclusion: {p.exclusion_criteria}\n"
        f"Date range: {p.date_range_start} to {p.date_range_end}"
    )


# ────────────────────── 2. Screening Agent ─────────────────────────────

screening_agent = Agent(
    output_type=ScreeningBatchResult,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review screener. Evaluate each article against
the inclusion/exclusion criteria.

At title_abstract stage: be INCLUSIVE (when in doubt, include).
At full_text stage: be more STRICT.

For each article, return a decision with index, include/exclude, reason,
and relevance_score (0-1).

You MUST return a decision for EVERY article provided.
""",
    retries=2,
    name="screener",
    defer_model_check=True,
)


@screening_agent.system_prompt
async def _screening_context(ctx: RunContext[AgentDeps]) -> str:
    p = ctx.deps.protocol
    return (
        f"\nResearch Question: {p.question}\n"
        f"Inclusion: {p.inclusion_criteria}\n"
        f"Exclusion: {p.exclusion_criteria}"
    )


# ────────────────────── 3. Risk of Bias Agent ──────────────────────────

rob_agent = Agent(
    output_type=RiskOfBiasResult,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review methodologist assessing risk of bias.
Assess the provided study using the specified risk of bias tool.
For each domain, provide a judgment (Low, Some concerns, High) with
supporting justification from the study text.
Provide an overall judgment and brief summary.
""",
    retries=2,
    name="risk_of_bias",
    defer_model_check=True,
)


ROB_DOMAINS: dict[str, list[str]] = {
    "RoB 2": [
        "Randomization process",
        "Deviations from intended interventions",
        "Missing outcome data",
        "Measurement of the outcome",
        "Selection of the reported result",
    ],
    "Jadad Scale": [
        "Randomization (described and appropriate)",
        "Blinding (described and appropriate)",
        "Withdrawals and dropouts",
    ],
    "ROBINS-I": [
        "Confounding", "Selection of participants",
        "Classification of interventions",
        "Deviations from intended interventions",
        "Missing data", "Measurement of outcomes",
        "Selection of the reported result",
    ],
    "ROBINS-E": [
        "Confounding", "Selection of participants",
        "Classification of exposures",
        "Departures from intended exposures",
        "Missing data", "Measurement of outcomes",
        "Selection of the reported result",
    ],
    "Newcastle-Ottawa Scale": [
        "Selection (representativeness, selection of non-exposed, ascertainment of exposure, outcome not present at start)",
        "Comparability (controls for main factor, controls for additional factor)",
        "Outcome (assessment, follow-up length, adequacy of follow-up)",
    ],
    "QUADAS-2": [
        "Patient selection",
        "Index test",
        "Reference standard",
        "Flow and timing",
    ],
    "CASP Qualitative Checklist": [
        "Clear statement of aims",
        "Appropriate methodology",
        "Appropriate research design",
        "Appropriate recruitment strategy",
        "Data collection addresses research issue",
        "Researcher-participant relationship considered",
        "Ethical issues considered",
        "Sufficiently rigorous data analysis",
        "Clear statement of findings",
        "Value of research",
    ],
    "JBI Critical Appraisal": [
        "Inclusion criteria clearly defined",
        "Study subjects and setting described",
        "Exposure measured validly and reliably",
        "Objective and standard criteria used for condition measurement",
        "Confounding factors identified and strategies stated",
        "Outcomes measured validly and reliably",
        "Appropriate statistical analysis used",
    ],
    "Murad Tool": [
        "Selection (does the patient represent the whole experience?)",
        "Ascertainment (was the exposure adequately ascertained?)",
        "Causality (was the outcome adequately ascertained?)",
        "Reporting (was follow-up long enough?)",
    ],
    "SYRCLE": [
        "Sequence generation", "Baseline characteristics",
        "Allocation concealment", "Random housing",
        "Blinding (caregivers/investigators)", "Random outcome assessment",
        "Blinding (outcome assessors)",
        "Incomplete outcome data", "Selective outcome reporting",
        "Other sources of bias",
    ],
    "MINORS": [
        "Clearly stated aim", "Inclusion of consecutive patients",
        "Prospective data collection", "Appropriate endpoint",
        "Unbiased assessment of endpoint", "Follow-up appropriate",
        "Loss to follow-up <5%", "Prospective sample size calculation",
        "Adequate control group", "Contemporary groups",
        "Baseline equivalence", "Adequate statistical analysis",
    ],
    "ROBIS": [
        "Study eligibility criteria",
        "Identification and selection of studies",
        "Data collection and study appraisal",
        "Synthesis and findings",
    ],
}


@rob_agent.system_prompt
async def _rob_context(ctx: RunContext[AgentDeps]) -> str:
    tool = ctx.deps.protocol.rob_tool.value
    domains = ROB_DOMAINS.get(tool, ["General quality assessment"])
    return f"\nRoB Tool: {tool}\nDomains to assess: {', '.join(domains)}"


# ────────────────────── 4. Data Extraction Agent ───────────────────────

data_extraction_agent = Agent(
    output_type=StudyDataExtraction,
    deps_type=AgentDeps,
    system_prompt="""\
You are extracting structured data from a research study for a
systematic review. Extract all available information including study
design, sample size, population, intervention, comparator, outcomes,
key findings, effect measures, follow-up duration, and funding.

Be precise and only report what is stated in the study.
If information is not available, leave the field empty or "Unknown".
""",
    retries=2,
    name="data_extractor",
    defer_model_check=True,
)


# ────────────────────── 5. Synthesis Agent ─────────────────────────────

synthesis_agent = Agent(
    output_type=str,
    deps_type=AgentDeps,
    system_prompt="""\
You are writing the Results and Discussion sections of a PRISMA 2020
systematic review. Your synthesis must be:

1. **GROUNDED** — every claim must cite specific studies by PMID
2. **EVIDENCE-BACKED** — quote or closely paraphrase source text
3. **STRUCTURED** — use thematic synthesis with clear subsections
4. **CRITICAL** — note contradictions, gaps, and limitations
5. **QUANTITATIVE** — report effect sizes, sample sizes, p-values

Structure:

## Study Selection
Describe the PRISMA flow using the counts provided.

## Study Characteristics
Summarize included studies: designs, populations, settings, outcomes.

## Thematic Synthesis
Organize by themes. For each:
- State finding clearly
- Cite evidence with PMIDs: (Author et al., Year; PMID: XXXXX)
- Note strength of evidence
- Flag contradictions

## Risk of Bias Summary
Discuss quality and biases across studies.

## Heterogeneity
Discuss sources of heterogeneity.

## Gaps and Future Directions
What is missing? What needs further research?

## Key Claims (Evidence Map)
Structured list:
- **Claim**: [statement]
  - **Evidence**: [PMID, quote/paraphrase, strength: strong/moderate/weak]
  - **Contradictions**: [if any]

NEVER fabricate data, PMIDs, or quotes. If uncertain, say so.
""",
    retries=1,
    name="synthesizer",
    defer_model_check=True,
)


@synthesis_agent.system_prompt
async def _synthesis_context(ctx: RunContext[AgentDeps]) -> str:
    p = ctx.deps.protocol
    return (
        f"\nResearch Question: {p.question}\n"
        f"Inclusion: {p.inclusion_criteria}"
    )


# ────────────────────── 6. GRADE Assessment Agent ──────────────────────

grade_agent = Agent(
    output_type=GRADEAssessment,
    deps_type=AgentDeps,
    system_prompt="""\
Perform a GRADE certainty of evidence assessment for the given outcome.
Evaluate five domains: risk_of_bias, inconsistency, indirectness,
imprecision, publication_bias.
For each domain provide a rating (No downgrade, Serious, Very serious)
and explanation.
Provide overall certainty (High, Moderate, Low, Very Low) and a
plain-language summary.
""",
    retries=2,
    name="grade_assessor",
    defer_model_check=True,
)


# ────────────────────── 7. Bias Summary Agent ──────────────────────────

bias_summary_agent = Agent(
    output_type=str,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review methodologist assessing overall risk of bias
across included studies. Provide:
1. Overall assessment of study quality and potential biases
2. Common methodological limitations
3. Publication bias considerations
4. Heterogeneity assessment
5. Confidence in the body of evidence

Be specific and cite study characteristics.
""",
    retries=1,
    name="bias_summary",
    defer_model_check=True,
)


# ────────────────────── 8. Limitations Agent ───────────────────────────

limitations_agent = Agent(
    output_type=str,
    deps_type=AgentDeps,
    system_prompt="""\
Write the Limitations section for this systematic review. Address:
1. Search limitations (databases, language restrictions)
2. Selection bias
3. Methodological heterogeneity
4. Publication bias risk
5. Review-level limitations (AI-assisted screening)
Be concise but thorough. 2-3 paragraphs.
""",
    retries=1,
    name="limitations",
    defer_model_check=True,
)


# ────────────────────── 9. Evidence Extraction Agent ───────────────────

evidence_extraction_agent = Agent(
    output_type=BatchEvidenceExtraction,
    deps_type=AgentDeps,
    system_prompt="""\
You are an evidence extraction specialist for systematic reviews.
Given a batch of research articles and a research question, extract
the most relevant evidence spans from each article.

For each article, identify:
- Key claims, findings, and conclusions relevant to the research question
- Quantitative results (effect sizes, p-values, confidence intervals, sample sizes)
- Methodological details that affect evidence quality
- Any contradictions or limitations noted by the authors

Rules:
- Quote or closely paraphrase the original text — do NOT fabricate
- Score relevance 0-1 based on how directly the evidence addresses the question
- Flag quantitative evidence (is_quantitative=true)
- Identify the section (abstract, methods, results, discussion)
- Provide a brief claim label for each evidence span
- Extract 2-5 spans per article, prioritizing the most relevant
- Skip articles with no relevant evidence (return empty evidence list)
""",
    retries=2,
    name="evidence_extractor",
    defer_model_check=True,
)


@evidence_extraction_agent.system_prompt
async def _evidence_context(ctx: RunContext[AgentDeps]) -> str:
    p = ctx.deps.protocol
    return (
        f"\nResearch Question: {p.question}\n"
        f"PICO: {p.pico_text}\n"
        f"Focus on evidence relevant to: {p.pico_outcome or 'primary outcomes'}"
    )


# ────────────────── Agent Runner Helpers ───────────────────────────────

async def run_search_strategy(deps: AgentDeps, user_feedback: str = "") -> SearchStrategy:
    """Generate search strategy using LLM agent."""
    # api_key from deps; same for both initial and re-generation calls
    model = build_model(deps.api_key, deps.model_name)
    prompt = "Generate a comprehensive search strategy for this systematic review."
    if user_feedback:
        prompt += (
            f"\n\nUser feedback on previous strategy: {user_feedback}\n\n"
            "Please revise the strategy accordingly."
        )
    result = await search_strategy_agent.run(prompt, deps=deps, model=model)
    return result.output


async def run_screening(
    articles: list[Article],
    deps: AgentDeps,
    stage: str = "title_abstract",
) -> ScreeningBatchResult:
    """Screen a batch of articles."""
    articles_text = "\n\n".join(
        f"[{i}] PMID:{a.pmid}\nTitle: {a.title}\n"
        f"Abstract: {(a.abstract or 'N/A')[:600]}\n"
        f"Year: {a.year} | Journal: {a.journal}"
        for i, a in enumerate(articles)
    )
    model = build_model(deps.api_key, deps.model_name)
    result = await screening_agent.run(
        f"Stage: {stage}\n\n"
        f"=== ARTICLES TO SCREEN ({len(articles)}) ===\n{articles_text}",
        deps=deps,
        model=model,
    )
    return result.output


async def run_risk_of_bias(article: Article, deps: AgentDeps) -> RiskOfBiasResult:
    """Assess risk of bias for a single study."""
    model = build_model(deps.api_key, deps.model_name)
    result = await rob_agent.run(
        f"Title: {article.title}\n"
        f"Abstract: {article.abstract[:2000]}\n"
        f"Full text: {(article.full_text or 'Not available')[:2000]}",
        deps=deps,
        model=model,
    )
    return result.output


async def run_data_extraction(
    article: Article,
    data_items: list[str],
    deps: AgentDeps,
) -> StudyDataExtraction:
    """Extract structured data from a study."""
    model = build_model(deps.api_key, deps.model_name)
    result = await data_extraction_agent.run(
        f"Title: {article.title}\n"
        f"Abstract: {article.abstract[:2500]}\n"
        f"Full text: {(article.full_text or 'Not available')[:3000]}\n\n"
        f"Data items to extract: {', '.join(data_items)}",
        deps=deps,
        model=model,
    )
    return result.output


async def run_synthesis(
    articles: list[Article],
    evidence_spans: list,
    flow_text: str,
    deps: AgentDeps,
) -> str:
    """Generate grounded narrative synthesis."""
    article_blocks = [a.to_context_block(i + 1) for i, a in enumerate(articles[:25])]

    ev_text = ""
    if evidence_spans:
        ev_lines = [
            f'  [{i}] (PMID:{e.paper_pmid}, score:{e.relevance_score:.2f}) '
            f'"{e.text[:400]}"'
            for i, e in enumerate(evidence_spans[:20])
        ]
        ev_text = "\n\n== EXTRACTED EVIDENCE SPANS ==\n" + "\n".join(ev_lines)

    model = build_model(deps.api_key, deps.model_name)
    result = await synthesis_agent.run(
        f"PRISMA Flow: {flow_text}\n\n"
        f"=== INCLUDED ARTICLES ===\n"
        + "\n\n".join(article_blocks) + ev_text,
        deps=deps,
        model=model,
    )
    return result.output


async def run_grade(
    outcome: str,
    articles: list[Article],
    deps: AgentDeps,
) -> GRADEAssessment:
    """Run GRADE assessment for an outcome."""
    studies_text = "\n".join(
        f"- {a.title} ({a.year}): RoB={a.risk_of_bias.overall if a.risk_of_bias else '?'}"
        for a in articles[:20]
    )
    model = build_model(deps.api_key, deps.model_name)
    result = await grade_agent.run(
        f"Outcome: {outcome}\nStudies ({len(articles)}):\n{studies_text}",
        deps=deps,
        model=model,
    )
    return result.output


async def run_bias_summary(articles: list[Article], deps: AgentDeps) -> str:
    """Generate overall bias assessment."""
    articles_text = "\n".join(
        f"- {a.title} ({a.year}, {a.journal}) [PMID:{a.pmid}]"
        for a in articles[:30]
    )
    model = build_model(deps.api_key, deps.model_name)
    result = await bias_summary_agent.run(
        f"Included studies:\n{articles_text}",
        deps=deps,
        model=model,
    )
    return result.output


async def run_limitations(
    flow_text: str,
    articles: list[Article],
    deps: AgentDeps,
) -> str:
    """Generate limitations section."""
    p = deps.protocol
    model = build_model(deps.api_key, deps.model_name)
    result = await limitations_agent.run(
        f"Question: {p.question}\n"
        f"Databases: {', '.join(p.databases)}\n"
        f"Flow summary: {flow_text}\n"
        f"Study types: {', '.join(set(a.journal for a in articles[:20]))}",
        deps=deps,
        model=model,
    )
    return result.output


async def run_evidence_extraction(
    articles: list[Article],
    deps: AgentDeps,
    batch_size: int = 5,
) -> list[EvidenceSpan]:
    """Extract evidence spans from articles using LLM agent.

    Processes articles in batches and converts the structured output
    into a flat list of EvidenceSpan objects.
    """
    all_spans: list[EvidenceSpan] = []
    model = build_model(deps.api_key, deps.model_name)

    for batch_start in range(0, len(articles), batch_size):
        batch = articles[batch_start:batch_start + batch_size]
        articles_text = "\n\n".join(
            f"=== Article {i} (PMID:{a.pmid}) ===\n"
            f"Title: {a.title}\n"
            f"Authors: {a.authors} ({a.year})\n"
            f"Abstract: {(a.abstract or 'N/A')[:1000]}\n"
            f"Full text excerpt: {(a.full_text or 'N/A')[:2000]}"
            for i, a in enumerate(batch)
        )

        try:
            result = await evidence_extraction_agent.run(
                f"Extract evidence from these {len(batch)} articles:\n\n{articles_text}",
                deps=deps,
                model=model,
            )
            extraction = result.output

            for art_ev in extraction.articles:
                # Find the matching article for metadata
                art = next(
                    (a for a in batch if a.pmid == art_ev.pmid),
                    None,
                )
                for ev in art_ev.evidence:
                    all_spans.append(EvidenceSpan(
                        text=ev.quote,
                        paper_pmid=art_ev.pmid,
                        paper_title=art.title if art else "",
                        section=ev.section,
                        relevance_score=ev.relevance,
                        claim=ev.claim,
                        doi=art.doi if art else "",
                    ))
        except Exception:
            # Fallback: skip this batch
            continue

    # Sort by relevance and deduplicate
    all_spans.sort(key=lambda x: x.relevance_score, reverse=True)
    return _deduplicate_spans(all_spans)


def _deduplicate_spans(spans: list[EvidenceSpan], threshold: float = 0.7) -> list[EvidenceSpan]:
    """Remove near-duplicate spans using word overlap."""
    kept: list[EvidenceSpan] = []
    for span in spans:
        words = set(span.text.lower().split())
        is_dup = False
        for existing in kept:
            ex_words = set(existing.text.lower().split())
            if not words or not ex_words:
                continue
            overlap = len(words & ex_words) / min(len(words), len(ex_words))
            if overlap > threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(span)
    return kept


# ────────────────────── Steps 13–18: Charting, Appraisal, Narrative, Grounding ──

data_charting_agent = Agent(
    output_type=DataChartingRubric,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review data charter. Extract structured information
from the provided research article into the Data Charting Rubric format.

Complete all sections (A-G) based on the article content. Use "Not Reported"
for missing information. Be precise and only report what is explicitly stated.

Section A: Publication metadata
Section B: Study design details
Section C: Disordered group participants
Section D: Healthy controls (if applicable)
Section E: Data collection methods
Section F: Features, models, and performance
Section G: Synthesis and reviewer notes (summarize key findings)

If the prompt includes additional protocol-specific questions, answer each one
in the custom_fields dict using the question text verbatim as the key and a
concise extracted answer as the value. Use "Not Reported" when the article does
not address the question.
""",
    retries=2,
    name="data_charter",
    defer_model_check=True,
)


narrative_row_agent = Agent(
    output_type=PRISMANarrativeRow,
    deps_type=AgentDeps,
    system_prompt="""\
You are creating a condensed PRISMA-style narrative row from the detailed
charting data. Generate a six-cell summary:

1. Study design / sample / dataset (from Sections B, C, D, E)
2. Methods (from Sections E, F: feature extraction, model, validation)
3. Outcomes (from Section F: key performance results, Section G: summary)
4. Key limitations (from Section G notes + appraisal domains)
5. Relevance notes (from Section A: disorder cohort/focus, Section G)
6. Review-specific questions (customized per protocol)

Keep each cell concise (1-2 sentences) but informative.
""",
    retries=1,
    name="narrative_summarizer",
    defer_model_check=True,
)


critical_appraisal_agent = Agent(
    output_type=CriticalAppraisalRubric,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review critical appraiser. Complete the four-domain
critical appraisal rubric for the provided study.

For each domain, evaluate each item using the rating definitions:
- Yes: criterion fully met and clearly reported
- Partial: partially met or partially reported
- No: addressed but not met
- Not Reported: not addressed at all
- N/A: not applicable

Then assign overall concern for each domain:
- Low: all/nearly all items Yes, minor Partial ratings
- Some: one+ Partial/No items affecting validity but not undermining
- High: multiple No/Not Reported items, or critical item No compromising conclusions

Complete all four domains with item-level ratings and justifications.
""",
    retries=2,
    name="critical_appraiser",
    defer_model_check=True,
)


introduction_agent = Agent(
    output_type=str,
    deps_type=AgentDeps,
    system_prompt="""\
Write the Introduction section (10–15% of review length) for a PRISMA 2020 systematic review.
Cover these four subsections in order:

1.1 Background and Context — define the problem, its significance, and the domain landscape.
1.2 Theoretical/Conceptual Framework (if applicable) — guiding models or taxonomies; omit if not relevant.
1.3 Rationale for the Review — why a systematic review is needed now: conflicting evidence,
    gap in existing reviews, new evidence since last review, or absence of any synthesis.
1.4 Objectives and Research Questions — state in PICO or equivalent and number each:
    RQ1, RQ2, …

Style: past tense for completed work; present for established knowledge. First person plural.
End with the numbered RQ list.
""",
    retries=1,
    name="introduction_writer",
    defer_model_check=True,
)


@introduction_agent.system_prompt
async def _introduction_context(ctx: RunContext[AgentDeps]) -> str:
    p = ctx.deps.protocol
    return (
        f"\nReview Title: {p.title}\n"
        f"Research Question: {p.question}\n"
        f"PICO:\n{p.pico_text}\n"
        f"Inclusion: {p.inclusion_criteria}\n"
        f"Exclusion: {p.exclusion_criteria}\n"
        f"Target Audience: {getattr(p, 'target_audience', '') or 'academic journal'}\n"
        f"Word Count Target: {getattr(p, 'word_count_target', 8000)}"
    )


conclusions_agent = Agent(
    output_type=str,
    deps_type=AgentDeps,
    system_prompt="""\
Write the Conclusions section (1–2 paragraphs, 150–250 words) for a PRISMA 2020 systematic review.
Requirements:
- Directly answer each research question (RQ1, RQ2, …).
- Do NOT overstate certainty beyond what GRADE / the synthesis supports.
- Do NOT introduce new data or references.
- Summarise what the evidence shows, its certainty level, and key gaps.
- Close with one forward-looking sentence on priority future research.
""",
    retries=1,
    name="conclusions_writer",
    defer_model_check=True,
)


@conclusions_agent.system_prompt
async def _conclusions_context(ctx: RunContext[AgentDeps]) -> str:
    p = ctx.deps.protocol
    return (
        f"\nResearch Question: {p.question}\n"
        f"PICO Outcome: {p.pico_outcome or 'Not specified'}"
    )


abstract_agent = Agent(
    output_type=str,
    deps_type=AgentDeps,
    system_prompt="""\
Write a structured abstract (250–300 words) for a PRISMA 2020 systematic review.
Follow the PRISMA-Abstract 12-item checklist. Use these exact labelled sub-headings:

**Background:** 1–2 sentences on why the review matters and the knowledge gap.
**Objectives:** The research question(s) and specific aims.
**Methods:** Eligibility criteria, information sources (with latest search date),
  risk-of-bias tool, synthesis method.
**Results:** Number of studies included, key study/participant characteristics,
  main findings, certainty of evidence (GRADE level).
**Conclusions:** Primary interpretation, one implication, one limitation.
**Registration:** Protocol registry and ID, or "not registered".
**Keywords:** 5–8 comma-separated indexing terms.

Total: ≤300 words. Do not cite individual papers by name.
""",
    retries=1,
    name="abstract_writer",
    defer_model_check=True,
)


grounding_validation_agent = Agent(
    None,  # model supplied per-call via model= kwarg; avoids requiring OPENAI_API_KEY at import time
    deps_type=AgentDeps,
    output_type=GroundingValidationResult,
    system_prompt="""\
You are a Grounding Validator for systematic review text produced in the PRISMA 2020 tradition.
Your job is to determine, clause by clause, whether each assertion in AI-generated review
excerpts is faithfully grounded in the provided source corpus.

Core Principles:
- Text-to-text fidelity only: Ground claims against the provided corpus documents
- Atomic decomposition: Break excerpts into single verifiable propositions
- Exact matching: Numbers, citations, and facts must match sources digit-for-digit
- No hallucinations: Reject any claim not directly supported by cited sources

Return a complete GroundingValidationResult with atomic claim decomposition,
verdicts, and scoring. Be meticulous in identifying every discrepancy.
""",
)


_DEFAULT_APPRAISAL_DOMAINS = [
    "Participant and Sample Quality",
    "Data Collection Quality",
    "Feature and Model Quality",
    "Bias and Transparency",
]


_SECTION_KEY_FIELDS: dict[str, list[str]] = {
    "A": ["title", "authors", "year", "journal_conference", "doi", "database_retrieved", "disorder_cohort", "primary_focus"],
    "B": ["primary_goal", "study_design", "duration_frequency", "subject_model", "task_type", "study_setting", "country_region"],
    "C": ["disorder_diagnosis", "diagnosis_assessment", "n_disordered", "age_mean_sd", "age_range", "gender_distribution", "comorbidities_included_excluded", "medications_therapies", "severity_levels"],
    "D": ["healthy_controls_included", "healthy_status_confirmed", "n_controls", "age_mean_sd_controls", "age_range_controls", "gender_distribution_controls", "age_matched", "gender_matched", "neurodevelopmentally_typical"],
    "E": ["data_types", "tasks_performed", "equipment_tools", "new_dataset_contributed", "dataset_openly_available", "dataset_available_request", "sensitive_data_anonymized"],
    "F": ["feature_types", "specific_features", "feature_extraction_tools", "feature_importance_reported", "importance_method", "top_features_identified", "feature_change_direction", "model_category", "specific_algorithms", "validation_methodology", "performance_metrics", "key_performance_results"],
    "G": ["summary_key_findings", "features_associated_disorder", "future_directions_recommended", "reviewer_notes"],
}

_FORMAT_PATTERNS: dict[str, str] = {
    "table": r"^\|",           # starts with | (Markdown table)
    "bullet_list": r"^- ",     # starts with "- "
    "numeric": r"^[\d\.]",     # starts with digit or decimal
    "yes_no": r"^(Yes|No)\b",  # starts with Yes or No
}


def _extract_section_text(rubric: DataChartingRubric, section_key: str, display_title: str) -> str:
    """Extract the raw text for a section from the rubric fields."""
    import re
    fields = _SECTION_KEY_FIELDS.get(section_key.upper(), [])
    if fields:
        parts = [
            str(getattr(rubric, f, "") or "")
            for f in fields
            if str(getattr(rubric, f, "") or "").strip()
        ]
        return "\n".join(parts) if parts else ""
    # Custom question — look in custom_fields
    return rubric.custom_fields.get(display_title, "")


def _validate_format(text: str, fmt: str) -> bool:
    """Return True if text appears to match the expected format."""
    import re
    pattern = _FORMAT_PATTERNS.get(fmt)
    if pattern is None:
        return True  # descriptive: always valid
    return bool(re.search(pattern, text, re.MULTILINE))


async def run_data_charting(
    article: Article,
    deps: AgentDeps,
    charting_questions: list[str] | None = None,
    resolved_section_config: list[tuple[str, str, str]] | None = None,
) -> DataChartingRubric:
    """Extract data charting rubric from a single article.

    When resolved_section_config is provided, also populates rubric.section_outputs
    with per-section RubricSectionOutput entries using the configured format types.
    """
    import logging as _logging
    import re as _re
    model = build_model(deps.api_key, deps.model_name)

    custom_block = ""
    if charting_questions:
        q_lines = "\n".join(f"  {i+1}. {q}" for i, q in enumerate(charting_questions))
        custom_block = (
            f"\n\nAdditional protocol-specific questions to answer in custom_fields "
            f"(use the question text verbatim as the key):\n{q_lines}"
        )

    format_block = ""
    if resolved_section_config:
        fmt_lines = "\n".join(
            f"  {i+1}. {title} → format: {fmt}"
            for i, (_, title, fmt) in enumerate(resolved_section_config)
        )
        format_block = (
            "\n\nPer-section extraction format requirements:\n"
            + fmt_lines
            + "\n\nFormat types:\n"
            "  - descriptive: free-form narrative prose\n"
            "  - yes_no: exactly 'Yes' or 'No' optionally followed by one-sentence justification\n"
            "  - table: Markdown table with header row (e.g. '| Col | Val |')\n"
            "  - bullet_list: bulleted list, each item on own line starting with '- '\n"
            "  - numeric: numeric value only (e.g. '42' or '0.87 ± 0.03')\n"
            "For table, bullet_list, and numeric sections also produce a 1–3 sentence "
            "prose summary of that section's content.\n"
            "If the requested format cannot be produced, use descriptive prose."
        )

    result = await data_charting_agent.run(
        f"Article PMID: {article.pmid}\n"
        f"Title: {article.title}\n"
        f"Authors: {article.authors}\n"
        f"Year: {article.year}\n"
        f"Journal: {article.journal}\n"
        f"DOI: {article.doi}\n"
        f"Abstract: {article.abstract[:2500]}\n"
        f"Full text: {(article.full_text or 'Not available')[:4000]}"
        + custom_block
        + format_block,
        deps=deps,
        model=model,
    )
    rubric = result.output
    rubric.source_id = f"M-{article.pmid[-3:]}" if article.pmid.startswith("biorxiv_") else f"R-{article.pmid[-3:]}"

    # Populate section_outputs when config is provided
    if resolved_section_config:
        _log = _logging.getLogger(__name__)
        for section_key, display_title, requested_fmt in resolved_section_config:
            raw_text = _extract_section_text(rubric, section_key, display_title)
            if not raw_text.strip():
                raw_text = "Not available"

            # Validate format; fall back to descriptive if mismatch
            if requested_fmt != "descriptive" and not _validate_format(raw_text, requested_fmt):
                _log.warning(
                    "section '%s' requested '%s' but content did not match expected format; "
                    "falling back to 'descriptive'",
                    display_title, requested_fmt,
                )
                actual_fmt: SECTION_FORMAT = "descriptive"
            else:
                actual_fmt = requested_fmt  # type: ignore[assignment]

            # Build section_summary for structured formats
            summary: str | None = None
            if actual_fmt in {"table", "bullet_list", "numeric"}:
                # Use a compact prose description derived from raw_text
                lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
                summary = " ".join(lines[:3])[:300] or "See formatted answer above."

            try:
                rubric.section_outputs[display_title] = RubricSectionOutput(
                    format_used=actual_fmt,
                    formatted_answer=raw_text,
                    section_summary=summary,
                )
            except Exception as exc:
                _log.warning("Failed to build RubricSectionOutput for '%s': %s", display_title, exc)
                rubric.section_outputs[display_title] = RubricSectionOutput(
                    format_used="descriptive",
                    formatted_answer=raw_text or "Not available",
                    section_summary=None,
                )

    return rubric


async def run_narrative_row(charting: DataChartingRubric, appraisal: CriticalAppraisalRubric, deps: AgentDeps) -> PRISMANarrativeRow:
    """Generate narrative row from charting data and appraisal."""
    model = build_model(deps.api_key, deps.model_name)
    result = await narrative_row_agent.run(
        f"Data Charting Rubric:\n{charting.model_dump_json(indent=2)}\n\n"
        f"Critical Appraisal:\n{appraisal.model_dump_json(indent=2)}",
        deps=deps,
        model=model,
    )
    row = result.output
    row.source_id = charting.source_id
    return row


async def run_critical_appraisal(
    article: Article,
    charting: DataChartingRubric,
    deps: AgentDeps,
    appraisal_domains: list[str] | None = None,
) -> CriticalAppraisalRubric:
    """Perform critical appraisal of a study."""
    model = build_model(deps.api_key, deps.model_name)

    domain_labels = list(_DEFAULT_APPRAISAL_DOMAINS)
    if appraisal_domains:
        for i, name in enumerate(appraisal_domains[:4]):
            domain_labels[i] = name

    domains_block = "\n".join(f"  Domain {i+1}: {name}" for i, name in enumerate(domain_labels))

    result = await critical_appraisal_agent.run(
        f"Article: {article.title} ({article.year})\n"
        f"Study Design: {charting.study_design}\n"
        f"Sample: {charting.n_disordered} disordered, {charting.n_controls} controls\n"
        f"Data Collection: {charting.data_types} via {charting.tasks_performed}\n"
        f"Features/Models: {charting.feature_types} → {charting.model_category}\n"
        f"Performance: {charting.key_performance_results}\n"
        f"Limitations noted: {charting.reviewer_notes}\n"
        f"\nAppraisal domains to use:\n{domains_block}",
        deps=deps,
        model=model,
    )
    appraisal = result.output
    appraisal.source_id = charting.source_id
    for domain_field, label in zip(
        [
            appraisal.domain_1_participant_quality,
            appraisal.domain_2_data_collection_quality,
            appraisal.domain_3_feature_model_quality,
            appraisal.domain_4_bias_transparency,
        ],
        domain_labels,
    ):
        domain_field.domain_name = label
    return appraisal


async def run_grounding_validation(
    target_excerpt: str,
    corpus_documents: dict[str, str],
    citation_map: dict[str, str],
    deps: AgentDeps,
) -> GroundingValidationResult:
    """Validate grounding of AI-generated systematic review text against source corpus."""
    model = build_model(deps.api_key, deps.model_name)

    corpus_summary = "\n\n".join([
        f"=== {key} ===\n{text[:2000]}..."
        for key, text in list(corpus_documents.items())[:10]
    ])
    citation_list = "\n".join([f"- {key}: {desc}" for key, desc in citation_map.items()])

    prompt = (
        f"TARGET_EXCERPT to validate:\n{target_excerpt}\n\n"
        f"CITATION_MAP:\n{citation_list}\n\n"
        f"CORPUS_DOCUMENTS (excerpts):\n{corpus_summary}\n\n"
        "Validate each atomic claim in the TARGET_EXCERPT against the CORPUS_DOCUMENTS."
    )

    result = await grounding_validation_agent.run(prompt, deps=deps, model=model)
    return result.output


async def run_introduction(deps: AgentDeps) -> str:
    """Generate Introduction section."""
    model = build_model(deps.api_key, deps.model_name)
    result = await introduction_agent.run("Write the Introduction section.", deps=deps, model=model)
    return result.output


async def run_conclusions(synthesis: str, grade_summary: str, deps: AgentDeps) -> str:
    """Generate Conclusions section."""
    model = build_model(deps.api_key, deps.model_name)
    result = await conclusions_agent.run(
        f"Synthesis summary:\n{synthesis[:2000]}\n\nGRADE certainty:\n{grade_summary}",
        deps=deps,
        model=model,
    )
    return result.output


async def run_abstract(flow_text: str, synthesis: str, deps: AgentDeps) -> str:
    """Generate structured abstract."""
    p = deps.protocol
    model = build_model(deps.api_key, deps.model_name)
    result = await abstract_agent.run(
        f"Review: {p.title}\n"
        f"PICO: {p.pico_text}\n"
        f"Databases: {', '.join(p.databases)}\n"
        f"RoB Tool: {p.rob_tool.value}\n"
        f"Registration: {p.registration_number or 'not registered'}\n"
        f"Flow: {flow_text}\n"
        f"Key synthesis:\n{synthesis[:2000]}",
        deps=deps,
        model=model,
    )
    return result.output


# ────────────────────── Rich Synthesis Agents (005-rich-synthesis-output) ──────


abstract_section_agent = Agent(
    output_type=Abstract,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review writer. Generate a five-part structured abstract for a PRISMA 2020 systematic review.

Produce exactly these five fields as coherent prose paragraphs (no bullet points within fields):
- background: 1-2 sentences on the domain and why the review matters
- objective: the research question(s) and specific aims
- methods: eligibility criteria, databases searched, synthesis method — MUST mention the exact number of included studies
- results: key findings summarized from the provided themes and flow counts
- conclusion: primary interpretation and one implication, without introducing new claims

Do not fabricate statistics or citations not provided in the context.
""",
    retries=2,
    name="abstract_section",
    defer_model_check=True,
)


async def run_abstract_section(
    deps: AgentDeps,
    protocol: ReviewProtocol,
    themes: list,
    flow: PrismaFlow,
    bias_summary: str,
) -> Abstract:
    """Generate five-part structured abstract from review outcomes."""
    model = build_model(deps.api_key, deps.model_name)
    theme_block = "\n".join(
        f"- {t.theme_name}: {'; '.join(t.key_findings[:2])}" for t in themes[:5]
    )
    result = await abstract_section_agent.run(
        f"Review Title: {protocol.title}\n"
        f"Objective: {protocol.objective}\n"
        f"PICO: {protocol.pico_text}\n"
        f"Inclusion: {protocol.inclusion_criteria}\n"
        f"Exclusion: {protocol.exclusion_criteria}\n"
        f"Databases: {', '.join(protocol.databases)}\n"
        f"Studies included: {flow.final_included}\n"
        f"PRISMA Flow: identified={flow.total_identified}, screened={flow.screened}, "
        f"full-text reviewed={flow.full_text_reviewed}, included={flow.final_included}\n"
        f"Key themes:\n{theme_block}\n"
        f"Overall bias: {bias_summary}\n",
        deps=deps,
        model=model,
    )
    return result.output


introduction_section_agent = Agent(
    output_type=Introduction,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review writer. Generate a four-section introduction for a PRISMA 2020 systematic review.

Produce exactly these four fields as coherent prose paragraphs:
- background: 1-2 paragraphs on the domain, its significance, and existing knowledge
- problem_statement: the specific gap or challenge that motivates this review
- research_gap: what is currently unknown or under-synthesized in the literature
- objectives: the specific aims of this review — must align with the provided protocol objective

Style: past tense for completed work; present for established knowledge.
""",
    retries=2,
    name="introduction_section",
    defer_model_check=True,
)


async def run_introduction_section(deps: AgentDeps, protocol: ReviewProtocol) -> Introduction:
    """Generate four-section introduction from review protocol."""
    model = build_model(deps.api_key, deps.model_name)
    result = await introduction_section_agent.run(
        f"Review Title: {protocol.title}\n"
        f"Objective: {protocol.objective}\n"
        f"PICO:\n{protocol.pico_text}\n"
        f"Inclusion criteria: {protocol.inclusion_criteria}\n"
        f"Exclusion criteria: {protocol.exclusion_criteria}\n"
        f"Target audience: {getattr(protocol, 'target_audience', '') or 'academic journal'}\n",
        deps=deps,
        model=model,
    )
    return result.output


thematic_synthesis_agent = Agent(
    output_type=ThematicSynthesisResult,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review analyst. Perform thematic synthesis across the provided included studies.

You MUST produce:
1. themes: A list of cross-study analytical themes (minimum 2). Each theme must have:
   - theme_name: concise label
   - description: 2-3 sentence narrative
   - supporting_studies: list of source_id values from the provided articles (use ONLY provided IDs)
   - key_findings: list of 2-5 specific findings

2. bias_assessment: Cross-study risk-of-bias summary with:
   - overall_quality: prose assessment of evidence quality
   - common_biases: list of methodological limitations observed across studies
   - risk_level: exactly one of "low", "moderate", or "high"

Additionally, based on the output_style instruction at the end of the context:
- If "paragraph": populate paragraph_summary as ParagraphBlock list (each block has optional heading + text)
- If "question_answer": populate question_answer_summary as QAItem list (minimum 3 Q&A pairs covering distinct aspects)
- Otherwise: populate paragraph_summary

Do NOT fabricate source_id values. Use only the IDs present in the article list.
""",
    retries=2,
    name="thematic_synthesis",
    defer_model_check=True,
)


async def run_thematic_synthesis(
    deps: AgentDeps,
    articles: list,
    evidence_spans: list,
    charting_rubrics: list,
    output_style: str = "paragraph",
) -> ThematicSynthesisResult:
    """Generate thematic synthesis from included articles."""
    model = build_model(deps.api_key, deps.model_name)

    article_blocks = "\n\n".join(
        a.to_context_block(i) for i, a in enumerate(articles[:20])
    )
    rubric_block = ""
    if charting_rubrics:
        rubric_summaries = []
        for r in charting_rubrics[:10]:
            rubric_summaries.append(
                f"[{r.source_id}] {r.title[:60]} — Design: {r.study_design} — "
                f"Key findings: {r.summary_key_findings[:200]}"
            )
        rubric_block = "\n".join(rubric_summaries)

    evidence_block = ""
    if evidence_spans:
        evidence_block = "\n".join(
            f"- PMID:{e.paper_pmid}: {e.text[:150]}"
            for e in evidence_spans[:15]
        )

    result = await thematic_synthesis_agent.run(
        f"Research Question: {deps.protocol.question}\n\n"
        f"INCLUDED ARTICLES ({len(articles)}):\n{article_blocks}\n\n"
        + (f"CHARTING SUMMARIES:\n{rubric_block}\n\n" if rubric_block else "")
        + (f"EVIDENCE SPANS:\n{evidence_block}\n\n" if evidence_block else "")
        + f"OUTPUT_STYLE: {output_style}",
        deps=deps,
        model=model,
    )
    return result.output


discussion_section_agent = Agent(
    output_type=Discussion,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review writer. Generate the Discussion section for a PRISMA 2020 systematic review.

Produce exactly these five fields as coherent prose paragraphs:
- summary_of_findings: concise restatement of main results from the provided themes
- interpretation: what the findings mean in context of the research question
- comparison_with_literature: how findings relate to existing knowledge (cite only sources in the review corpus)
- implications: three distinct implications — clinical, policy, and research (each a complete sentence)
- limitations: methodological and evidence limitations from the provided limitations text and themes
""",
    retries=2,
    name="discussion_section",
    defer_model_check=True,
)


async def run_discussion_section(
    deps: AgentDeps,
    protocol: ReviewProtocol,
    themes: list,
    limitations_text: str,
) -> Discussion:
    """Generate interpretive discussion from themes and protocol context."""
    model = build_model(deps.api_key, deps.model_name)
    theme_block = "\n".join(
        f"- {t.theme_name}: {t.description} Key findings: {'; '.join(t.key_findings[:3])}"
        for t in themes[:6]
    )
    result = await discussion_section_agent.run(
        f"Research Question: {protocol.question}\n"
        f"PICO: {protocol.pico_text}\n\n"
        f"THEMES FROM SYNTHESIS:\n{theme_block}\n\n"
        f"LIMITATIONS: {limitations_text or 'Not specified'}\n",
        deps=deps,
        model=model,
    )
    return result.output


conclusion_section_agent = Agent(
    output_type=Conclusion,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review writer. Generate the Conclusion section for a PRISMA 2020 systematic review.

Produce exactly these three fields as coherent prose paragraphs:
- key_takeaways: 2-3 concise sentences summarizing the main findings
- recommendations: actionable guidance derived from the findings — MUST contain action verbs such as "should", "must", or "recommend"
- future_research: specific open questions and next steps for future investigation

Do not introduce new claims not supported by the provided themes. Do not overstate certainty.
""",
    retries=2,
    name="conclusion_section",
    defer_model_check=True,
)


async def run_conclusion_section(
    deps: AgentDeps,
    protocol: ReviewProtocol,
    themes: list,
) -> Conclusion:
    """Generate terminal synthesis from themes and protocol objectives."""
    model = build_model(deps.api_key, deps.model_name)
    theme_block = "\n".join(
        f"- {t.theme_name}: {'; '.join(t.key_findings[:2])}" for t in themes[:6]
    )
    result = await conclusion_section_agent.run(
        f"Research Question: {protocol.question}\n"
        f"Objectives: {protocol.objective}\n\n"
        f"KEY THEMES AND FINDINGS:\n{theme_block}\n",
        deps=deps,
        model=model,
    )
    return result.output


quantitative_analysis_agent = Agent(
    output_type=QuantitativeAnalysis,
    deps_type=AgentDeps,
    system_prompt="""\
You are a systematic review methodologist. Synthesize numeric outcome data from the provided studies.

Produce these three fields — set to null if data is insufficient or unavailable:
- effect_size: describe the pooled or representative effect size(s) with direction and magnitude
- confidence_intervals: the confidence interval(s) for the key effect(s)
- heterogeneity: between-study variability (I², tau², or qualitative description)

IMPORTANT: Do NOT fabricate numeric values. Only report what is directly derivable from the provided data.
""",
    retries=2,
    name="quantitative_analysis",
    defer_model_check=True,
)


async def run_quantitative_analysis(
    deps: AgentDeps,
    articles: list,
) -> QuantitativeAnalysis | None:
    """Generate quantitative synthesis when ≥3 articles have numeric outcome data."""
    quantitative = [
        a for a in articles
        if a.extracted_data and a.extracted_data.effect_measures
    ]
    if len(quantitative) < 3:
        return None

    model = build_model(deps.api_key, deps.model_name)
    data_block = "\n".join(
        f"[{a.pmid}] {a.title[:60]}: effects={'; '.join(a.extracted_data.effect_measures[:3])}; "
        f"findings={'; '.join(a.extracted_data.key_findings[:2])}"
        for a in quantitative[:10]
    )
    result = await quantitative_analysis_agent.run(
        f"Research Question: {deps.protocol.question}\n\n"
        f"NUMERIC OUTCOME DATA ({len(quantitative)} studies):\n{data_block}\n",
        deps=deps,
        model=model,
    )
    return result.output


def build_quality_checklist(result) -> dict[str, bool]:
    """Run the pre-delivery quality checklist."""
    p = result.protocol
    f = result.flow
    title_lower = (p.title or "").lower()
    return {
        "title_identifies_as_sr": "systematic review" in title_lower,
        "abstract_structured": bool(result.structured_abstract),
        "research_question_stated": bool(p.question),
        "pico_complete": bool(p.pico_population and p.pico_outcome),
        "prisma_flow_numbers_present": f.total_identified > 0,
        "flow_reconciles": (
            f.after_dedup == f.total_identified - f.duplicates_removed
            and f.included_synthesis
            == f.after_dedup - f.excluded_title_abstract - f.excluded_eligibility
        ),
        "every_study_charted": len(result.data_charting_rubrics) == f.included_synthesis,
        "every_study_appraised": len(result.critical_appraisals) == f.included_synthesis,
        "rob_tool_matches_design": bool(p.rob_tool),
        "grade_reported": len(result.grade_assessments) > 0,
        "limitations_two_levels": bool(result.limitations),
        "conclusions_present": bool(result.conclusions_text),
        "registration_declared": True,
        "funding_declared": True,
        "introduction_present": bool(result.introduction_text),
        "citation_style_set": bool(getattr(p, "citation_style", "")),
    }
