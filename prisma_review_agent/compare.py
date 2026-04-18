"""
Multi-model compare mode for the PRISMA Review Agent.

Runs the same review protocol in parallel across N models, shares article
acquisition (Steps 1–6), computes field-level agreement on structured charting
output, generates a consensus synthesis, and assembles a CompareReviewResult.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime

from .agents import (
    AgentDeps,
    build_quality_checklist,
    default_appraisal_config,
    default_charting_template,
    run_abstract,
    run_bias_summary,
    run_conclusions,
    run_consensus_synthesis,
    run_critical_appraisal,
    run_data_charting,
    run_data_extraction,
    run_grade,
    run_grounding_validation,
    run_introduction,
    run_limitations,
    run_narrative_row,
    run_risk_of_bias,
    run_screening,
    run_synthesis,
)
from .evidence import extract_evidence
from .models import (
    Article,
    # Feature 007
    CompareReviewResult,
    CriticalAppraisalResult,
    FieldAgreement,
    MergedReviewResult,
    ModelReviewRun,
    PRISMAFlowCounts,
    PRISMAReviewResult,
    ReviewProtocol,
    ScreeningDecisionType,
    ScreeningLogEntry,
    ScreeningStage,
    SynthesisDivergence,
)

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ = True
except ImportError:
    _RAPIDFUZZ = False

logger = logging.getLogger(__name__)

_FALLBACK_CONSENSUS = "Insufficient successful model runs for consensus synthesis."


# ─── per-model LLM pipeline (Steps 7–15) ──────────────────────────────────────

async def _run_model_pipeline(
    pipeline: object,  # PRISMAReviewPipeline — typed as object to avoid circular import
    deduped: list[Article],
    model_name: str,
    proto: ReviewProtocol,
    *,
    data_items: list[str] | None = None,
    up: Callable[[str], None] = lambda _: None,
    art_store: object = None,
) -> PRISMAReviewResult:
    """Run Steps 7–15 for one model using the pre-fetched article pool.

    Uses a model-specific AgentDeps so every LLM call goes to the correct model
    while sharing the HTTP clients (pubmed, biorxiv) from `pipeline`.
    """
    from collections import defaultdict

    deps = AgentDeps(
        protocol=proto,
        api_key=pipeline.deps.api_key,  # type: ignore[attr-defined]
        model_name=model_name,
    )

    flow = PRISMAFlowCounts(
        total_identified=pipeline._last_acq_flow.total_identified,  # type: ignore[attr-defined]
        duplicates_removed=pipeline._last_acq_flow.duplicates_removed,  # type: ignore[attr-defined]
        after_dedup=len(deduped),
        screened_title_abstract=len(deduped),
    )

    all_screening: list[ScreeningLogEntry] = []
    pubmed = pipeline.pubmed  # type: ignore[attr-defined]

    # ── 7. Title/abstract screening ──
    up(f"[{model_name}] Screening {len(deduped)} articles...")
    ta_included: list[Article] = []
    ta_excluded: list[Article] = []
    articles_copy = [a.model_copy() for a in deduped]

    for batch_start in range(0, len(articles_copy), 15):
        batch = articles_copy[batch_start:batch_start + 15]
        try:
            batch_result = await run_screening(batch, deps, "title_abstract")
            for dec in batch_result.decisions:
                if 0 <= dec.index < len(batch):
                    art = batch[dec.index]
                    art.quality_score = dec.relevance_score
                    all_screening.append(ScreeningLogEntry(
                        pmid=art.pmid, title=art.title,
                        decision=dec.decision, reason=dec.reason,
                        stage=ScreeningStage.TITLE_ABSTRACT,
                    ))
                    if dec.decision == ScreeningDecisionType.INCLUDE:
                        art.inclusion_status = "included"
                        ta_included.append(art)
                    else:
                        art.inclusion_status = "excluded"
                        art.exclusion_reason = dec.reason
                        ta_excluded.append(art)
            covered = {s.pmid for s in all_screening if s.stage == ScreeningStage.TITLE_ABSTRACT}
            for art in batch:
                if art.pmid not in covered:
                    ta_included.append(art)
        except Exception as e:
            up(f"[{model_name}] Screening batch failed ({e}), auto-including {len(batch)}")
            ta_included.extend(batch)

    flow.excluded_title_abstract = len(ta_excluded)

    if not ta_included:
        return PRISMAReviewResult(
            research_question=proto.question, protocol=proto, flow=flow,
            screening_log=all_screening,
            synthesis_text="All articles excluded during title/abstract screening.",
            timestamp=datetime.now().isoformat(),
        )

    # ── 8. Full-text retrieval (reuse pre-populated full_text from acquisition) ──
    flow.sought_fulltext = len(ta_included)

    if art_store:
        try:
            pmids_needed = [a.pmid for a in ta_included if not a.full_text and a.pmc_id]
            if pmids_needed:
                cached_arts = await art_store.get_by_pmids(pmids_needed)
                cached_map = {a.pmid: a for a in cached_arts}
                for art in ta_included:
                    if art.pmid in cached_map and cached_map[art.pmid].full_text:
                        art.full_text = cached_map[art.pmid].full_text
        except Exception as exc:
            logger.warning("[%s] ArticleStore pre-populate failed: %s", model_name, exc)

    pmc_articles = [a for a in ta_included if a.pmc_id and not a.full_text]
    if pmc_articles:
        texts = pubmed.fetch_full_text([a.pmc_id for a in pmc_articles])
        for art in pmc_articles:
            if art.pmc_id in texts:
                art.full_text = texts[art.pmc_id]

    flow.not_retrieved = len(ta_included) - len(
        [a for a in ta_included if a.full_text or a.abstract]
    )

    # ── 9. Full-text eligibility screening ──
    ft_articles = [a for a in ta_included if a.full_text]
    no_ft = [a for a in ta_included if not a.full_text]
    ft_included = list(no_ft)
    ft_excluded: list[Article] = []

    if ft_articles:
        for batch_start in range(0, len(ft_articles), 10):
            batch = ft_articles[batch_start:batch_start + 10]
            try:
                batch_result = await run_screening(batch, deps, "full_text")
                for dec in batch_result.decisions:
                    if 0 <= dec.index < len(batch):
                        art = batch[dec.index]
                        all_screening.append(ScreeningLogEntry(
                            pmid=art.pmid, title=art.title,
                            decision=dec.decision, reason=dec.reason,
                            stage=ScreeningStage.FULL_TEXT,
                        ))
                        if dec.decision == ScreeningDecisionType.INCLUDE:
                            ft_included.append(art)
                        else:
                            art.inclusion_status = "excluded"
                            art.exclusion_reason = dec.reason
                            ft_excluded.append(art)
            except Exception as e:
                up(f"[{model_name}] FT screening batch failed ({e}), auto-including")
                ft_included.extend(batch)

    flow.assessed_eligibility = len(ta_included)
    flow.excluded_eligibility = len(ft_excluded)
    flow.included_synthesis = len(ft_included)

    reason_counts: dict[str, int] = defaultdict(int)
    for a in ta_excluded + ft_excluded:
        r = a.exclusion_reason[:60] if a.exclusion_reason else "Unspecified"
        reason_counts[r] += 1
    flow.excluded_reasons = dict(
        sorted(reason_counts.items(), key=lambda x: -x[1])[:8]
    )

    if not ft_included:
        return PRISMAReviewResult(
            research_question=proto.question, protocol=proto, flow=flow,
            screening_log=all_screening,
            synthesis_text="All articles excluded during eligibility assessment.",
            timestamp=datetime.now().isoformat(),
        )

    # ── 10. Evidence extraction ──
    evidence = await extract_evidence(ft_included, deps)

    # ── 11. Per-study data extraction ──
    if data_items:
        for art in ft_included:
            try:
                art.extracted_data = await run_data_extraction(art, data_items, deps)
            except Exception as e:
                logger.warning("[%s] Data extraction failed for %s: %s", model_name, art.pmid, e)

    # ── 12. Risk of bias ──
    for art in ft_included:
        try:
            art.risk_of_bias = await run_risk_of_bias(art, deps)
        except Exception as e:
            logger.warning("[%s] RoB failed for %s: %s", model_name, art.pmid, e)

    # ── 13. Synthesis ──
    flow_text = (
        f"Identified: {flow.total_identified} | After dedup: {flow.after_dedup} | "
        f"Screened: {flow.screened_title_abstract} | Included: {flow.included_synthesis}"
    )
    synthesis = await run_synthesis(ft_included, evidence, flow_text, deps)

    # ── 14. Bias, GRADE, limitations (parallel) ──
    outcome_text = proto.pico_outcome or "Primary outcome"
    outcomes = [o.strip() for o in outcome_text.split(",") if o.strip()]
    grade_tasks = {
        outcome: run_grade(outcome, ft_included, deps) for outcome in outcomes[:3]
    }
    bias_result, limitations_result, *grade_results = await asyncio.gather(
        run_bias_summary(ft_included, deps),
        run_limitations(flow_text, ft_included, deps),
        *grade_tasks.values(),
        return_exceptions=True,
    )
    bias_text = bias_result if isinstance(bias_result, str) else ""
    limitations_text = limitations_result if isinstance(limitations_result, str) else ""
    grade_assessments = {}
    for outcome, res in zip(grade_tasks.keys(), grade_results):
        from .models import GRADEAssessment
        if isinstance(res, GRADEAssessment):
            grade_assessments[outcome] = res

    # ── Steps 13–18: Charting + appraisal ──
    charting_template = proto.charting_template or default_charting_template()
    appraisal_config = proto.critical_appraisal_config or default_appraisal_config()
    charting_questions = list(proto.charting_questions) if proto.charting_questions else None
    appraisal_domains = list(proto.appraisal_domains) if proto.appraisal_domains else None

    data_charting_rubrics = []
    critical_appraisals = []
    critical_appraisal_results: list[CriticalAppraisalResult] = []
    narrative_rows = []

    from .pipeline import _resolve_section_config
    _resolved_cfg = _resolve_section_config(proto)

    for article in ft_included:
        try:
            rubric = await run_data_charting(
                article, deps, charting_questions,
                resolved_section_config=_resolved_cfg,
                charting_template=charting_template,
            )
            data_charting_rubrics.append(rubric)
            appraisal_rubric, appraisal_result = await run_critical_appraisal(
                article, rubric, deps, appraisal_domains,
                appraisal_config=appraisal_config,
            )
            critical_appraisals.append(appraisal_rubric)
            critical_appraisal_results.append(appraisal_result)
            row = await run_narrative_row(rubric, appraisal_rubric, deps)
            narrative_rows.append(row)
        except Exception as exc:
            logger.warning(
                "[%s] Charting/appraisal failed for %s: %s",
                model_name, article.pmid, exc,
            )

    # Grounding validation
    grounding_validation = None
    try:
        corpus = {a.pmid: (a.abstract or "") + " " + (a.full_text or "") for a in ft_included}
        citation_map = {a.pmid: f"{a.authors} ({a.year})" for a in ft_included}
        grounding_validation = await run_grounding_validation(synthesis, corpus, citation_map, deps)
    except Exception as exc:
        logger.warning("[%s] Grounding validation failed: %s", model_name, exc)

    # Document sections
    grade_summary = "; ".join(
        f"{k}: {v.overall_certainty.value}" for k, v in grade_assessments.items()
    )
    short_flow = f"{flow.total_identified} identified, {flow.included_synthesis} included"
    try:
        introduction_text, conclusions_text, structured_abstract = await asyncio.gather(
            run_introduction(deps),
            run_conclusions(synthesis, grade_summary, deps),
            run_abstract(short_flow, synthesis, deps),
        )
    except Exception:
        introduction_text = conclusions_text = structured_abstract = ""

    final_result = PRISMAReviewResult(
        research_question=proto.question,
        protocol=proto,
        flow=flow,
        included_articles=ft_included,
        screening_log=all_screening,
        evidence_spans=evidence,
        synthesis_text=synthesis,
        bias_assessment=bias_text,
        limitations=limitations_text,
        grade_assessments=grade_assessments,
        timestamp=datetime.now().isoformat(),
        data_charting_rubrics=data_charting_rubrics,
        narrative_rows=narrative_rows,
        critical_appraisals=critical_appraisals,
        grounding_validation=grounding_validation,
        structured_abstract=structured_abstract,
        introduction_text=introduction_text,
        conclusions_text=conclusions_text,
        structured_appraisal_results=critical_appraisal_results,
    )
    final_result.quality_checklist = build_quality_checklist(final_result)

    # Rich PrismaReview assembly
    if final_result.included_articles:
        try:
            from .pipeline import assemble_prisma_review
            final_result.prisma_review = await assemble_prisma_review(
                final_result, deps, "paragraph", resolved_config=_resolved_cfg,
            )
        except Exception as exc:
            logger.warning("[%s] PrismaReview assembly failed: %s", model_name, exc)

    return final_result


# ─── field agreement computation ──────────────────────────────────────────────

def _compute_field_agreement(
    model_results: list[ModelReviewRun],
) -> dict[str, FieldAgreement]:
    """Compute per-field agreement across successful model runs.

    Key format: "{source_id}::{section_key}::{field_name}"
    Comparison rules:
      - enumerated / yes_no_extended: case-insensitive exact match
      - free_text / numeric: rapidfuzz token_set_ratio >= 80 (falls back to exact if unavailable)
    reviewer_only fields are excluded.
    """
    succeeded = [r for r in model_results if r.succeeded and r.result is not None]
    if len(succeeded) < 2:
        return {}

    # Build {key: {model_name: (value, answer_type)}} from all runs
    combined: dict[str, dict[str, tuple[str, str]]] = {}
    for run in succeeded:
        result = run.result
        assert result is not None
        for study_report in result.structured_appraisal_results:
            source_id = study_report.source_id
        # Walk field_answers from data_charting_rubrics via StudyDataExtractionReport
        for sder in (result.prisma_review.methods.data_extraction if result.prisma_review else []):
            for section_key, sec_result in sder.field_answers.items():
                for fa in sec_result.field_answers:
                    key = f"{sder.source_id}::{section_key}::{fa.field_name}"
                    if key not in combined:
                        combined[key] = {}
                    answer_type = getattr(fa, "answer_type", "free_text")
                    combined[key][run.model_name] = (fa.value or "", answer_type)

    # Determine agreement for each key with ≥ 2 models
    agreement: dict[str, FieldAgreement] = {}
    for key, model_values in combined.items():
        if len(model_values) < 2:
            continue
        source_id, section_key, field_name = key.split("::", 2)
        values_map = {m: v for m, (v, _) in model_values.items()}
        answer_type = next(iter(model_values.values()))[1]
        value_list = list(values_map.values())

        agreed = _all_agree(value_list, answer_type)
        agreement[key] = FieldAgreement(
            field_name=field_name,
            section_key=section_key,
            source_id=source_id,
            agreed=agreed,
            values=values_map,
            answer_type=answer_type,
        )

    return agreement


def _all_agree(values: list[str], answer_type: str) -> bool:
    """Return True iff all values are considered equivalent."""
    if len(values) < 2:
        return True
    if answer_type in ("enumerated", "yes_no_extended"):
        normalized = [v.strip().lower() for v in values]
        return all(v == normalized[0] for v in normalized)
    # free_text / numeric — fuzzy match
    if _RAPIDFUZZ:
        for i in range(len(values)):
            for j in range(i + 1, len(values)):
                if _fuzz.token_set_ratio(values[i], values[j]) < 80:
                    return False
        return True
    # fallback without rapidfuzz
    normalized = [v.strip().lower() for v in values]
    return all(v == normalized[0] for v in normalized)


# ─── run_compare() ─────────────────────────────────────────────────────────────

async def run_compare(
    pipeline: object,  # PRISMAReviewPipeline
    models: list[str],
    *,
    progress_callback: Callable[[str], None] | None = None,
    data_items: list[str] | None = None,
    auto_confirm: bool = False,
    confirm_callback: Callable | None = None,
    max_plan_iterations: int = 3,
    consensus_model: str | None = None,
) -> CompareReviewResult:
    """Run the full review in parallel for each model in *models*.

    Steps 1–6 (article acquisition) run once and are shared.
    Steps 7–15 (LLM analysis) run independently per model via asyncio.gather.
    """
    # Validate model list
    unique_models = list(dict.fromkeys(models))
    if len(unique_models) < 2:
        raise ValueError("run_compare() requires at least 2 unique model names")
    if len(unique_models) > 5:
        raise ValueError("run_compare() supports at most 5 models")
    if len(unique_models) < len(models):
        logger.warning(
            "run_compare: duplicate model names removed. Using: %s", unique_models
        )

    proto = pipeline.protocol  # type: ignore[attr-defined]

    def up(msg: str) -> None:
        pipeline.log(msg)  # type: ignore[attr-defined]
        if progress_callback:
            progress_callback(msg)

    # ── Steps 1–6: shared acquisition ──
    up("=== Compare mode: running shared article acquisition ===")
    acq = await pipeline._fetch_articles(  # type: ignore[attr-defined]
        proto, up,
        auto_confirm=auto_confirm,
        confirm_callback=confirm_callback,
        max_plan_iterations=max_plan_iterations,
    )
    # Stash flow on pipeline so _run_model_pipeline can read it
    pipeline._last_acq_flow = acq.flow  # type: ignore[attr-defined]

    art_store = getattr(pipeline, "_compare_art_store", None)

    # ── Steps 7–15: per-model LLM pipeline (parallel) ──
    up(f"=== Starting {len(unique_models)} parallel model runs ===")
    tasks = [
        _run_model_pipeline(
            pipeline, acq.deduped, m, proto,
            data_items=data_items, up=up, art_store=art_store,
        )
        for m in unique_models
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    model_runs: list[ModelReviewRun] = []
    for model_name, raw in zip(unique_models, raw_results):
        if isinstance(raw, BaseException):
            up(f"[{model_name}] Run FAILED: {raw}")
            model_runs.append(ModelReviewRun(model_name=model_name, error=str(raw)))
        else:
            up(f"[{model_name}] Run complete ({len(raw.included_articles)} included)")
            model_runs.append(ModelReviewRun(model_name=model_name, result=raw))

    succeeded = [r for r in model_runs if r.succeeded]
    failed = [r for r in model_runs if not r.succeeded]

    # ── Field agreement ──
    field_agreement = _compute_field_agreement(model_runs)
    up(f"Field agreement computed: {len(field_agreement)} fields")

    # ── Consensus synthesis ──
    consensus_text = _FALLBACK_CONSENSUS
    divergences: list[SynthesisDivergence] = []
    if len(succeeded) >= 2:
        _consensus_model = consensus_model or unique_models[0]
        try:
            syntheses = {
                r.model_name: (r.result.synthesis_text or "")
                for r in succeeded
                if r.result is not None
            }
            consensus_out = await run_consensus_synthesis(
                syntheses,
                AgentDeps(
                    protocol=proto,
                    api_key=pipeline.deps.api_key,  # type: ignore[attr-defined]
                    model_name=_consensus_model,
                ),
            )
            consensus_text = consensus_out.consensus_text
            divergences = consensus_out.divergences
            up("Consensus synthesis complete.")
        except Exception as exc:
            logger.warning("Consensus synthesis failed: %s", exc)
            consensus_text = _FALLBACK_CONSENSUS

    merged = MergedReviewResult(
        consensus_synthesis=consensus_text,
        field_agreement=field_agreement,
        synthesis_divergences=divergences,
        models_included=[r.model_name for r in succeeded],
        models_failed=[r.model_name for r in failed],
    )

    return CompareReviewResult(
        protocol=proto,
        compare_models=unique_models,
        model_results=model_runs,
        merged=merged,
        timestamp=datetime.now().isoformat(),
    )
