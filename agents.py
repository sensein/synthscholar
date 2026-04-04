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

from models import (
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

async def run_search_strategy(deps: AgentDeps) -> SearchStrategy:
    """Generate search strategy using LLM agent."""
    model = build_model(deps.api_key, deps.model_name)
    result = await search_strategy_agent.run(
        "Generate a comprehensive search strategy for this systematic review.",
        deps=deps,
        model=model,
    )
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
