"""
PRISMA Review Pipeline — async orchestrator.

Runs the full 15-step PRISMA 2020 pipeline using pydantic-ai agents
for LLM tasks and plain HTTP clients for data acquisition.
"""

from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import logging

from .models import (
    Article,
    InclusionStatus,
    ReviewProtocol,
    PRISMAFlowCounts,
    PRISMAReviewResult,
    ScreeningLogEntry,
    ScreeningStage,
    ScreeningDecisionType,
    EvidenceSpan,
    GRADEAssessment,
    ReviewPlan,
    PlanRejectedError,
    MaxIterationsReachedError,
    PrismaReview,
    PrismaFlow,
    Methods,
    Results,
    Discussion,
    Conclusion,
    Abstract,
    Introduction,
    Implications,
    DataExtractionField,
    DataExtractionSchema,
    SourceMetadata,
    StudyDesign,
    ExtractedStudy,
    OutputFormat,
    OptionalSection,
    BiasAssessment,
    Theme,
    BUILTIN_SECTIONS,
    StudyDataExtractionReport,
    SectionExtractionResult,
    FieldAnswer,
    CriticalAppraisalResult,
    CompareReviewResult,
)
from .clients import Cache, PubMedClient, BioRxivClient
from .evidence import extract_evidence
from .agents import (
    AgentDeps,
    run_search_strategy,
    run_screening,
    run_risk_of_bias,
    run_data_extraction,
    run_synthesis,
    run_grade,
    run_bias_summary,
    run_limitations,
    run_data_charting,
    run_narrative_row,
    run_critical_appraisal,
    run_grounding_validation,
    run_introduction,
    run_conclusions,
    run_abstract,
    build_quality_checklist,
    run_abstract_section,
    run_introduction_section,
    run_thematic_synthesis,
    run_discussion_section,
    run_conclusion_section,
    run_quantitative_analysis,
    default_charting_template,
    default_appraisal_config,
)

try:
    from .cache.store import CacheStore
    from .cache.article_store import ArticleStore
    from .cache.skill import cache_lookup, cache_store
    from .cache.models import CacheUnavailableError, CacheSchemaError
    _CACHE_AVAILABLE = True
except ImportError:
    _CACHE_AVAILABLE = False

logger = logging.getLogger(__name__)


@dataclass
class AcquisitionResult:
    """Output of the shared article-acquisition phase (Steps 1–6)."""
    deduped: list[Article]
    all_search_queries: list[str]
    flow: PRISMAFlowCounts


class PRISMAReviewPipeline:
    """Full PRISMA 2020 pipeline with async agent orchestration."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "anthropic/claude-sonnet-4",
        ncbi_api_key: str = "",
        protocol: Optional[ReviewProtocol] = None,
        enable_cache: bool = True,
        max_per_query: int = 20,
        related_depth: int = 1,
        biorxiv_days: int = 180,
    ):
        self.cache = Cache() if enable_cache else None
        self.pubmed = PubMedClient(api_key=ncbi_api_key, cache=self.cache)
        self.biorxiv = BioRxivClient(self.cache)
        self.protocol = protocol or ReviewProtocol()
        self.max_per_query = max_per_query
        self.related_depth = related_depth
        self.biorxiv_days = biorxiv_days
        self.model_name = model_name
        self.deps = AgentDeps(
            protocol=self.protocol,
            api_key=api_key,
            model_name=model_name,
        )
        self._log: list[str] = []

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self._log.append(entry)
        print(entry)

    async def run(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,  # existing — unchanged
        data_items: Optional[list[str]] = None,                     # existing — unchanged
        auto_confirm: bool = False,           # new — False: show confirmation gate; True: skip it
        confirm_callback: Optional[Callable[[ReviewPlan], "bool | str"]] = None,  # new — None: CLI input or auto
        max_plan_iterations: int = 3,         # new — max re-generation attempts before MaxIterationsReachedError
        output_synthesis_style: str = "paragraph",  # new — controls PrismaReview results rendering style: "paragraph" | "question_answer" | "bullet_list" | "table"
        assemble_timeout: float = 3600.0,     # max seconds for the two-wave assembly gather; raises asyncio.TimeoutError on breach
    ) -> PRISMAReviewResult:
        if not self.deps.api_key:
            raise ValueError(
                "api_key is required — set OPENROUTER_API_KEY or pass api_key to PRISMAReviewPipeline"
            )
        proto = self.protocol
        flow = PRISMAFlowCounts()
        all_screening: list[ScreeningLogEntry] = []
        all_articles: dict[str, Article] = {}
        seen_pmids: set[str] = set()

        def up(msg: str):
            self.log(msg)
            if progress_callback:
                progress_callback(msg)

        # ── 0. PostgreSQL review-result cache check ──
        pg_store: Optional[CacheStore] = None
        art_store: Optional[ArticleStore] = None
        criteria_dict = proto.model_dump()

        if _CACHE_AVAILABLE and proto.pg_dsn:
            try:
                pg_store = CacheStore(dsn=proto.pg_dsn)
                await pg_store.connect()
                art_store = ArticleStore(dsn=proto.pg_dsn)
                await art_store.connect()

                if not proto.force_refresh:
                    up("Checking review cache...")
                    lookup = await cache_lookup(
                        pg_store, criteria_dict, self.model_name,
                        threshold=proto.cache_threshold,
                        ttl_days=proto.cache_ttl_days,
                        owner_review_id=proto.review_id,
                    )
                    if lookup.hit and lookup.entry:
                        score = lookup.similarity_score or 1.0
                        matched = lookup.entry.criteria_json.get("title", "")
                        up(
                            f"Cache HIT (similarity={score:.1%}) — matched: '{matched}' "
                            f"[cached {lookup.entry.created_at.strftime('%Y-%m-%d')}]"
                        )
                        cached_result = PRISMAReviewResult.model_validate(
                            lookup.entry.result_json
                        )
                        cached_result.cache_hit = True
                        cached_result.cache_similarity_score = score
                        cached_result.cache_matched_criteria = lookup.entry.criteria_json
                        await pg_store.close()
                        await art_store.close()
                        return cached_result
                    else:
                        up("No shareable cache found — running fresh search.")
                else:
                    up("Force-refresh: skipping cache lookup.")
            except (CacheUnavailableError, CacheSchemaError) as exc:
                logger.warning("Cache unavailable (%s) — continuing without cache.", exc)
                pg_store = None
                art_store = None
            except Exception as exc:
                logger.warning("Cache check failed (%s) — continuing without cache.", exc)
                pg_store = None
                art_store = None

        # ── 1. Search strategy (LLM agent) ──
        up("Generating search strategy...")
        strategy = await run_search_strategy(self.deps)
        up(f"Strategy: {len(strategy.pubmed_queries)} PubMed + {len(strategy.biorxiv_queries)} bioRxiv queries")
        up(f"Rationale: {strategy.rationale}")

        # ── 1a. Plan confirmation checkpoint ──
        # TTY detection: auto-confirm in non-interactive environments when no callback given
        _effective_auto = auto_confirm
        if not auto_confirm and confirm_callback is None and not sys.stdin.isatty():
            logger.warning(
                "Non-interactive environment detected; defaulting to auto_confirm=True"
            )
            _effective_auto = True

        if not _effective_auto and confirm_callback is not None:
            for iteration in range(1, max_plan_iterations + 1):
                plan = _build_review_plan(strategy, proto.question, iteration=iteration)
                up(f"Awaiting plan confirmation (iteration {iteration})...")
                response = confirm_callback(plan)
                if response is True or response == "":
                    up("Plan approved.")
                    break
                elif response is False:
                    raise PlanRejectedError(iterations=iteration)
                else:
                    feedback = str(response)
                    up(f"Revising strategy with feedback: {feedback[:80]}...")
                    strategy = await run_search_strategy(self.deps, user_feedback=feedback)
            else:
                raise MaxIterationsReachedError(iteration, max_plan_iterations)

        pubmed_queries = strategy.pubmed_queries or [proto.question]
        biorxiv_queries = strategy.biorxiv_queries or []
        all_search_queries = pubmed_queries + biorxiv_queries

        # ── 2. PubMed search ──
        for i, q in enumerate(pubmed_queries):
            up(f"PubMed search {i+1}/{len(pubmed_queries)}: {q[:60]}...")
            pmids = self.pubmed.search(
                q, self.max_per_query,
                proto.date_range_start, proto.date_range_end,
            )
            new = [p for p in pmids if p not in seen_pmids]
            seen_pmids.update(new)
            if new:
                arts = self.pubmed.fetch_articles(new)
                for a in arts:
                    a.source = "pubmed_search"
                    all_articles[a.pmid] = a
                up(f"  Found {len(arts)} articles")
        flow.db_pubmed = sum(
            1 for a in all_articles.values() if a.source == "pubmed_search"
        )

        # ── 3. bioRxiv search ──
        if "bioRxiv" in proto.databases and biorxiv_queries:
            for bq in biorxiv_queries[:3]:
                up(f"bioRxiv search: {bq[:60]}...")
                bx_arts = self.biorxiv.search(bq, 10, self.biorxiv_days)
                for a in bx_arts:
                    if a.pmid not in all_articles:
                        all_articles[a.pmid] = a
                up(f"  Found {len(bx_arts)} preprints")
        flow.db_biorxiv = sum(
            1 for a in all_articles.values() if a.source == "biorxiv"
        )

        # ── 4. Related articles ──
        pm_pmids = [
            a.pmid for a in all_articles.values()
            if not a.pmid.startswith("biorxiv_")
        ]
        related_total = 0
        if pm_pmids:
            seeds = pm_pmids[:8]
            for d in range(1, self.related_depth + 1):
                up(f"Finding related articles (depth {d})...")
                rel_pmids = self.pubmed.find_related(seeds, max_results=15)
                new_rel = [p for p in rel_pmids if p not in all_articles]
                if new_rel:
                    rel_arts = self.pubmed.fetch_articles(new_rel)
                    for a in rel_arts:
                        a.source = f"related_{d}"
                        all_articles[a.pmid] = a
                    related_total += len(rel_arts)
                    seeds = [a.pmid for a in rel_arts[:5]]
                    up(f"  Depth {d}: {len(rel_arts)} articles")
                else:
                    break
        flow.db_related = related_total

        # ── 5. Multi-hop citation navigation ──
        hop_total = 0
        if proto.max_hops > 0 and pm_pmids:
            hop_seeds = pm_pmids[:5]
            for hop in range(1, proto.max_hops + 1):
                up(f"Citation hop {hop}...")
                back = self.pubmed.find_related(hop_seeds, max_results=8)
                fwd = self.pubmed.find_cited_by(hop_seeds, max_results=8)
                combined = list(set(back + fwd))
                new_hop = [p for p in combined if p not in all_articles]
                if new_hop:
                    hop_arts = self.pubmed.fetch_articles(new_hop[:15])
                    for a in hop_arts:
                        a.source = f"hop_{hop}"
                        a.hop_level = hop
                        a.parent_id = ",".join(hop_seeds[:3])
                        all_articles[a.pmid] = a
                    hop_total += len(hop_arts)
                    hop_seeds = [a.pmid for a in hop_arts[:5]]
                    up(f"  Hop {hop}: {len(hop_arts)} articles")
                else:
                    break
        flow.db_hops = hop_total
        flow.total_identified = len(all_articles)
        up(f"Total identified: {flow.total_identified}")

        # ── 6. Deduplication ──
        up("Deduplicating...")
        unique_map: dict[str, Article] = {}
        for a in all_articles.values():
            key = a.doi.lower().strip() if a.doi else a.pmid
            if key not in unique_map:
                unique_map[key] = a
        deduped = list(unique_map.values())
        flow.duplicates_removed = flow.total_identified - len(deduped)
        flow.after_dedup = len(deduped)
        up(f"After dedup: {flow.after_dedup} (removed {flow.duplicates_removed})")

        if not deduped:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto, flow=flow,
                synthesis_text="No articles found matching the search criteria.",
                timestamp=datetime.now().isoformat(),
            )

        # ── 7. Title/abstract screening (LLM agent, batches of 15) ──
        up(f"Screening {len(deduped)} articles (title/abstract)...")
        flow.screened_title_abstract = len(deduped)
        ta_included: list[Article] = []
        ta_excluded: list[Article] = []

        for batch_start in range(0, len(deduped), 15):
            batch = deduped[batch_start:batch_start + 15]
            try:
                batch_result = await run_screening(batch, self.deps, "title_abstract")
                for dec in batch_result.decisions:
                    if 0 <= dec.index < len(batch):
                        art = batch[dec.index]
                        art.quality_score = dec.relevance_score
                        all_screening.append(ScreeningLogEntry(
                            pmid=art.pmid, title=art.title,
                            decision=dec.decision,
                            reason=dec.reason,
                            stage=ScreeningStage.TITLE_ABSTRACT,
                        ))
                        if dec.decision == ScreeningDecisionType.INCLUDE:
                            art.inclusion_status = InclusionStatus.INCLUDED
                            ta_included.append(art)
                        else:
                            art.inclusion_status = InclusionStatus.EXCLUDED
                            art.exclusion_reason = dec.reason
                            ta_excluded.append(art)
                # Auto-include any missed articles
                covered = {s.pmid for s in all_screening if s.stage == ScreeningStage.TITLE_ABSTRACT}
                for art in batch:
                    if art.pmid not in covered:
                        ta_included.append(art)
                        all_screening.append(ScreeningLogEntry(
                            pmid=art.pmid, title=art.title,
                            decision=ScreeningDecisionType.INCLUDE,
                            reason="Not evaluated — auto-included",
                            stage=ScreeningStage.TITLE_ABSTRACT,
                        ))
            except Exception as e:
                up(f"  Screening batch failed ({e}), auto-including {len(batch)}")
                for art in batch:
                    ta_included.append(art)
                    all_screening.append(ScreeningLogEntry(
                        pmid=art.pmid, title=art.title,
                        decision=ScreeningDecisionType.INCLUDE,
                        reason=f"Auto-included (error: {str(e)[:50]})",
                        stage=ScreeningStage.TITLE_ABSTRACT,
                    ))

        flow.excluded_title_abstract = len(ta_excluded)
        up(f"Screening: {len(ta_included)} included, {len(ta_excluded)} excluded")

        if not ta_included:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto, search_queries=all_search_queries,
                flow=flow, screening_log=all_screening,
                synthesis_text="All articles excluded during title/abstract screening.",
                timestamp=datetime.now().isoformat(),
            )

        # ── 8. Full-text retrieval ──
        flow.sought_fulltext = len(ta_included)

        # 8a. Pre-populate full_text from ArticleStore to reduce API calls
        if art_store:
            try:
                pmids_needed = [a.pmid for a in ta_included if not a.full_text and a.pmc_id]
                if pmids_needed:
                    cached_arts = await art_store.get_by_pmids(pmids_needed)
                    cached_map = {a.pmid: a for a in cached_arts}
                    prefilled = 0
                    for a in ta_included:
                        if a.pmid in cached_map and cached_map[a.pmid].full_text:
                            a.full_text = cached_map[a.pmid].full_text
                            prefilled += 1
                    if prefilled:
                        up(f"  Pre-filled {prefilled} full texts from article store")
            except Exception as exc:
                logger.warning("ArticleStore pre-populate failed (%s) — continuing.", exc)

        pmc_articles = [a for a in ta_included if a.pmc_id and not a.full_text]
        if pmc_articles:
            up(f"Fetching full text for {len(pmc_articles)} PMC articles...")
            texts = self.pubmed.fetch_full_text([a.pmc_id for a in pmc_articles])
            for a in pmc_articles:
                if a.pmc_id in texts:
                    a.full_text = texts[a.pmc_id]
            up(f"  Retrieved {len(texts)} full texts")

        # 8b. Persist all fetched articles to ArticleStore for future reuse
        if art_store:
            try:
                n = await art_store.upsert_articles(ta_included)
                up(f"  Stored {n} articles in article store")
            except Exception as exc:
                logger.warning("ArticleStore upsert failed (%s) — continuing.", exc)

        flow.not_retrieved = len(ta_included) - len(
            [a for a in ta_included if a.full_text or a.abstract]
        )

        # ── 9. Full-text eligibility screening ──
        ft_articles = [a for a in ta_included if a.full_text]
        no_ft = [a for a in ta_included if not a.full_text]
        ft_included = list(no_ft)
        ft_excluded: list[Article] = []

        if ft_articles:
            up(f"Full-text eligibility screening ({len(ft_articles)} articles)...")
            for batch_start in range(0, len(ft_articles), 10):
                batch = ft_articles[batch_start:batch_start + 10]
                try:
                    batch_result = await run_screening(batch, self.deps, "full_text")
                    for dec in batch_result.decisions:
                        if 0 <= dec.index < len(batch):
                            art = batch[dec.index]
                            all_screening.append(ScreeningLogEntry(
                                pmid=art.pmid, title=art.title,
                                decision=dec.decision,
                                reason=dec.reason,
                                stage=ScreeningStage.FULL_TEXT,
                            ))
                            if dec.decision == ScreeningDecisionType.INCLUDE:
                                ft_included.append(art)
                            else:
                                art.inclusion_status = InclusionStatus.EXCLUDED
                                art.exclusion_reason = dec.reason
                                ft_excluded.append(art)
                except Exception as e:
                    up(f"  FT screening batch failed ({e}), auto-including")
                    ft_included.extend(batch)

        flow.assessed_eligibility = len(ta_included)
        flow.excluded_eligibility = len(ft_excluded)
        flow.included_synthesis = len(ft_included)

        # Tally exclusion reasons
        reason_counts: dict[str, int] = defaultdict(int)
        for a in ta_excluded + ft_excluded:
            r = a.exclusion_reason[:60] if a.exclusion_reason else "Unspecified"
            reason_counts[r] += 1
        flow.excluded_reasons = dict(
            sorted(reason_counts.items(), key=lambda x: -x[1])[:8]
        )

        up(f"Final included: {flow.included_synthesis} articles")

        if not ft_included:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto, search_queries=all_search_queries,
                flow=flow, screening_log=all_screening,
                synthesis_text="All articles excluded during eligibility assessment.",
                timestamp=datetime.now().isoformat(),
            )

        # ── 10. Evidence span extraction (no LLM) ──
        up("Extracting evidence spans...")
        evidence = await extract_evidence(ft_included, self.deps)
        up(f"Extracted {len(evidence)} evidence spans")

        # ── 11. Per-study data extraction (LLM agent) ──
        if data_items:
            up(f"Extracting data from {len(ft_included)} studies...")
            for i, art in enumerate(ft_included):
                up(f"  [{i+1}/{len(ft_included)}] {art.title[:50]}...")
                try:
                    art.extracted_data = await run_data_extraction(
                        art, data_items, self.deps
                    )
                except Exception as e:
                    up(f"  Data extraction failed for {art.pmid}: {e}")

        # ── 12. Per-study risk of bias (LLM agent) ──
        up(f"Assessing risk of bias ({proto.rob_tool.value})...")
        for i, art in enumerate(ft_included):
            up(f"  [{i+1}/{len(ft_included)}] {art.title[:50]}...")
            try:
                art.risk_of_bias = await run_risk_of_bias(art, self.deps)
            except Exception as e:
                up(f"  RoB failed for {art.pmid}: {e}")

        # ── 13. Grounded synthesis (LLM agent) ──
        flow_text = (
            f"Identified: {flow.total_identified} | "
            f"After dedup: {flow.after_dedup} | "
            f"Screened: {flow.screened_title_abstract} | "
            f"Excluded (screening): {flow.excluded_title_abstract} | "
            f"Full-text assessed: {flow.assessed_eligibility} | "
            f"Excluded (eligibility): {flow.excluded_eligibility} | "
            f"Included: {flow.included_synthesis}"
        )
        up(f"Synthesizing {len(ft_included)} articles...")
        synthesis = await run_synthesis(ft_included, evidence, flow_text, self.deps)

        # ── 14. Overall bias assessment + GRADE (LLM agents, parallel) ──
        up("Assessing overall bias and GRADE...")
        bias_task = run_bias_summary(ft_included, self.deps)

        outcome_text = proto.pico_outcome or "Primary outcome"
        outcomes = [o.strip() for o in outcome_text.split(",") if o.strip()]
        grade_tasks = {
            outcome: run_grade(outcome, ft_included, self.deps)
            for outcome in outcomes[:3]
        }

        limitations_task = run_limitations(flow_text, ft_included, self.deps)

        # Run bias, GRADE, and limitations concurrently
        bias_result, limitations_result, *grade_results = await asyncio.gather(
            bias_task,
            limitations_task,
            *grade_tasks.values(),
            return_exceptions=True,
        )

        bias_text = bias_result if isinstance(bias_result, str) else ""
        limitations_text = limitations_result if isinstance(limitations_result, str) else ""
        grade_assessments: dict[str, GRADEAssessment] = {}
        for outcome, result in zip(grade_tasks.keys(), grade_results):
            if isinstance(result, GRADEAssessment):
                grade_assessments[outcome] = result

        # ── Steps 13–18: Charting, Appraisal, Narrative, Grounding, Document sections ──

        charting_questions = list(proto.charting_questions) if proto.charting_questions else None
        appraisal_domains = list(proto.appraisal_domains) if proto.appraisal_domains else None

        # Feature 006: resolve template and config (None → factory default applied per-call)
        charting_template = proto.charting_template or default_charting_template()
        appraisal_config = proto.critical_appraisal_config or default_appraisal_config()

        data_charting_rubrics = []
        critical_appraisals = []
        critical_appraisal_results: list[CriticalAppraisalResult] = []
        narrative_rows = []

        _resolved_cfg = _resolve_section_config(self.protocol)

        for article in ft_included:
            try:
                up(f"Charting article {article.pmid}…")
                rubric = await run_data_charting(
                    article, self.deps, charting_questions,
                    resolved_section_config=_resolved_cfg,
                    charting_template=charting_template,
                )
                data_charting_rubrics.append(rubric)

                appraisal_rubric, appraisal_result = await run_critical_appraisal(
                    article, rubric, self.deps, appraisal_domains,
                    appraisal_config=appraisal_config,
                )
                critical_appraisals.append(appraisal_rubric)
                critical_appraisal_results.append(appraisal_result)

                row = await run_narrative_row(rubric, appraisal_rubric, self.deps)
                narrative_rows.append(row)
            except Exception as exc:
                logger.warning("Charting/appraisal failed for %s: %s", article.pmid, exc)

        # Grounding validation
        grounding_validation = None
        try:
            up("Validating grounding of synthesis text…")
            corpus: dict[str, str] = {
                a.pmid: (a.abstract or "") + " " + (a.full_text or "")
                for a in ft_included
            }
            citation_map = {a.pmid: f"{a.authors} ({a.year})" for a in ft_included}
            grounding_validation = await run_grounding_validation(
                synthesis, corpus, citation_map, self.deps
            )
            up(
                f"Grounding validation: {grounding_validation.overall_verdict} "
                f"({grounding_validation.grounding_rate:.1%} supported)"
            )
        except Exception as exc:
            logger.warning("Grounding validation failed: %s", exc)

        # Introduction, Conclusions, Abstract (parallel)
        grade_summary = "; ".join(
            f"{k}: {v.overall_certainty.value}" for k, v in grade_assessments.items()
        )
        flow_text = (
            f"{flow.total_identified} identified, {flow.included_synthesis} included"
        )
        try:
            up("Generating document sections…")
            introduction_text, conclusions_text, structured_abstract = await asyncio.gather(
                run_introduction(self.deps),
                run_conclusions(synthesis, grade_summary, self.deps),
                run_abstract(flow_text, synthesis, self.deps),
            )
        except Exception as exc:
            logger.warning("Document section generation failed: %s", exc)
            introduction_text = conclusions_text = structured_abstract = ""

        up("Review complete!")

        final_result = PRISMAReviewResult(
            research_question=proto.question,
            protocol=proto,
            search_queries=all_search_queries,
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

        # ── Assemble rich PrismaReview report ──
        if final_result.included_articles:
            try:
                up("Assembling structured PrismaReview report...")
                final_result.prisma_review = await assemble_prisma_review(
                    final_result, self.deps, output_synthesis_style,
                    resolved_config=_resolved_cfg,
                    progress_callback=up,
                    assemble_timeout=assemble_timeout,
                )
                pr = final_result.prisma_review
                # Backfill plain-text fields for backward compatibility
                final_result.synthesis_text = final_result.synthesis_text or "\n\n".join(
                    t.description for t in pr.results.themes
                )
                final_result.structured_abstract = final_result.structured_abstract or (
                    f"Background: {pr.abstract.background}\n"
                    f"Objective: {pr.abstract.objective}\n"
                    f"Methods: {pr.abstract.methods}\n"
                    f"Results: {pr.abstract.results}\n"
                    f"Conclusion: {pr.abstract.conclusion}"
                )
                final_result.introduction_text = final_result.introduction_text or (
                    f"{pr.introduction.background}\n\n"
                    f"{pr.introduction.problem_statement}\n\n"
                    f"{pr.introduction.objectives}"
                )
                final_result.conclusions_text = final_result.conclusions_text or (
                    f"{pr.conclusion.key_takeaways}\n\n{pr.conclusion.recommendations}"
                )
                up(f"PrismaReview assembled: {len(pr.results.themes)} themes, "
                   f"{len(pr.results.extracted_studies or [])} studies")
            except Exception as exc:
                logger.warning("PrismaReview assembly failed: %s", exc)

        # ── Persist result to PostgreSQL cache ──
        if pg_store:
            try:
                await cache_store(
                    pg_store,
                    criteria_dict,
                    self.model_name,
                    final_result.model_dump(mode="json"),
                    threshold=proto.cache_threshold,
                    ttl_days=proto.cache_ttl_days,
                    review_id=proto.review_id,
                    is_shared=proto.share_to_cache,
                )
                up("Result stored in review cache.")
            except Exception as exc:
                logger.warning("Failed to store result in cache (%s).", exc)
            finally:
                await pg_store.close()

        if art_store:
            try:
                await art_store.close()
            except Exception:
                pass

        return final_result

    # ── Feature 007: shared acquisition + per-model LLM steps ──────────

    async def _fetch_articles(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,
        auto_confirm: bool = True,
        confirm_callback: Optional[Callable[[ReviewPlan], "bool | str"]] = None,
        max_plan_iterations: int = 3,
    ) -> AcquisitionResult:
        """Steps 1–6: search strategy, HTTP searches, deduplication.

        Used by run_compare() to share article acquisition across all model runs.
        Does not interact with PostgreSQL cache.
        """
        proto = self.protocol
        flow = PRISMAFlowCounts()
        all_articles: dict[str, Article] = {}
        seen_pmids: set[str] = set()

        def up(msg: str) -> None:
            self.log(msg)
            if progress_callback:
                progress_callback(msg)

        # 1. Search strategy (LLM)
        up("Generating search strategy...")
        strategy = await run_search_strategy(self.deps)
        up(f"Strategy: {len(strategy.pubmed_queries)} PubMed + {len(strategy.biorxiv_queries)} bioRxiv queries")

        # 1a. Plan confirmation
        _effective_auto = auto_confirm
        if not auto_confirm and confirm_callback is None and not sys.stdin.isatty():
            _effective_auto = True

        if not _effective_auto and confirm_callback is not None:
            for iteration in range(1, max_plan_iterations + 1):
                plan = _build_review_plan(strategy, proto.question, iteration=iteration)
                up(f"Awaiting plan confirmation (iteration {iteration})...")
                response = confirm_callback(plan)
                if response is True or response == "":
                    up("Plan approved.")
                    break
                elif response is False:
                    raise PlanRejectedError(iterations=iteration)
                else:
                    feedback = str(response)
                    up(f"Revising strategy with feedback: {feedback[:80]}...")
                    strategy = await run_search_strategy(self.deps, user_feedback=feedback)
            else:
                raise MaxIterationsReachedError(iteration, max_plan_iterations)

        pubmed_queries = strategy.pubmed_queries or [proto.question]
        biorxiv_queries = strategy.biorxiv_queries or []
        all_search_queries = pubmed_queries + biorxiv_queries

        # 2. PubMed search
        for i, q in enumerate(pubmed_queries):
            up(f"PubMed search {i+1}/{len(pubmed_queries)}: {q[:60]}...")
            pmids = self.pubmed.search(q, self.max_per_query, proto.date_range_start, proto.date_range_end)
            new = [p for p in pmids if p not in seen_pmids]
            seen_pmids.update(new)
            if new:
                arts = self.pubmed.fetch_articles(new)
                for a in arts:
                    a.source = "pubmed_search"
                    all_articles[a.pmid] = a
                up(f"  Found {len(arts)} articles")
        flow.db_pubmed = sum(1 for a in all_articles.values() if a.source == "pubmed_search")

        # 3. bioRxiv search
        if "bioRxiv" in proto.databases and biorxiv_queries:
            for bq in biorxiv_queries[:3]:
                up(f"bioRxiv search: {bq[:60]}...")
                bx_arts = self.biorxiv.search(bq, 10, self.biorxiv_days)
                for a in bx_arts:
                    if a.pmid not in all_articles:
                        all_articles[a.pmid] = a
                up(f"  Found {len(bx_arts)} preprints")
        flow.db_biorxiv = sum(1 for a in all_articles.values() if a.source == "biorxiv")

        # 4. Related articles
        pm_pmids = [a.pmid for a in all_articles.values() if not a.pmid.startswith("biorxiv_")]
        related_total = 0
        if pm_pmids:
            seeds = pm_pmids[:8]
            for d in range(1, self.related_depth + 1):
                up(f"Finding related articles (depth {d})...")
                rel_pmids = self.pubmed.find_related(seeds, max_results=15)
                new_rel = [p for p in rel_pmids if p not in all_articles]
                if new_rel:
                    rel_arts = self.pubmed.fetch_articles(new_rel)
                    for a in rel_arts:
                        a.source = f"related_{d}"
                        all_articles[a.pmid] = a
                    related_total += len(rel_arts)
                    seeds = [a.pmid for a in rel_arts[:5]]
                    up(f"  Depth {d}: {len(rel_arts)} articles")
                else:
                    break
        flow.db_related = related_total

        # 5. Citation hops
        hop_total = 0
        if proto.max_hops > 0 and pm_pmids:
            hop_seeds = pm_pmids[:5]
            for hop in range(1, proto.max_hops + 1):
                up(f"Citation hop {hop}...")
                back = self.pubmed.find_related(hop_seeds, max_results=8)
                fwd = self.pubmed.find_cited_by(hop_seeds, max_results=8)
                combined = list(set(back + fwd))
                new_hop = [p for p in combined if p not in all_articles]
                if new_hop:
                    hop_arts = self.pubmed.fetch_articles(new_hop[:15])
                    for a in hop_arts:
                        a.source = f"hop_{hop}"
                        a.hop_level = hop
                        a.parent_id = ",".join(hop_seeds[:3])
                        all_articles[a.pmid] = a
                    hop_total += len(hop_arts)
                    hop_seeds = [a.pmid for a in hop_arts[:5]]
                    up(f"  Hop {hop}: {len(hop_arts)} articles")
                else:
                    break
        flow.db_hops = hop_total
        flow.total_identified = len(all_articles)
        up(f"Total identified: {flow.total_identified}")

        # 6. Deduplication
        up("Deduplicating...")
        unique_map: dict[str, Article] = {}
        for a in all_articles.values():
            key = a.doi.lower().strip() if a.doi else a.pmid
            if key not in unique_map:
                unique_map[key] = a
        deduped = list(unique_map.values())
        flow.duplicates_removed = flow.total_identified - len(deduped)
        flow.after_dedup = len(deduped)
        up(f"After dedup: {flow.after_dedup} (removed {flow.duplicates_removed})")

        return AcquisitionResult(deduped=deduped, all_search_queries=all_search_queries, flow=flow)

    async def _run_from_deduped(
        self,
        deduped: list[Article],
        all_search_queries: list[str],
        initial_flow: PRISMAFlowCounts,
        *,
        progress_callback: Optional[Callable[[str], None]] = None,
        data_items: Optional[list[str]] = None,
        output_synthesis_style: str = "paragraph",
        assemble_timeout: float = 3600.0,
    ) -> PRISMAReviewResult:
        """Steps 7–15: LLM screening, synthesis, charting, appraisal, assembly.

        Uses self.deps.model_name for all LLM calls so per-model runs use the right model.
        Does not interact with PostgreSQL cache.
        """
        import copy
        proto = self.protocol
        flow = copy.copy(initial_flow)
        all_screening: list[ScreeningLogEntry] = []

        def up(msg: str) -> None:
            self.log(msg)
            if progress_callback:
                progress_callback(msg)

        if not deduped:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto,
                search_queries=all_search_queries,
                flow=flow,
                synthesis_text="No articles found matching the search criteria.",
                timestamp=datetime.now().isoformat(),
            )

        # 7. Title/abstract screening
        up(f"Screening {len(deduped)} articles (title/abstract)...")
        flow.screened_title_abstract = len(deduped)
        ta_included: list[Article] = []
        ta_excluded: list[Article] = []

        for batch_start in range(0, len(deduped), 15):
            batch = deduped[batch_start:batch_start + 15]
            try:
                batch_result = await run_screening(batch, self.deps, "title_abstract")
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
                            art.inclusion_status = InclusionStatus.INCLUDED
                            ta_included.append(art)
                        else:
                            art.inclusion_status = InclusionStatus.EXCLUDED
                            art.exclusion_reason = dec.reason
                            ta_excluded.append(art)
                covered = {s.pmid for s in all_screening if s.stage == ScreeningStage.TITLE_ABSTRACT}
                for art in batch:
                    if art.pmid not in covered:
                        ta_included.append(art)
                        all_screening.append(ScreeningLogEntry(
                            pmid=art.pmid, title=art.title,
                            decision=ScreeningDecisionType.INCLUDE,
                            reason="Not evaluated — auto-included",
                            stage=ScreeningStage.TITLE_ABSTRACT,
                        ))
            except Exception as e:
                up(f"  Screening batch failed ({e}), auto-including {len(batch)}")
                for art in batch:
                    ta_included.append(art)
                    all_screening.append(ScreeningLogEntry(
                        pmid=art.pmid, title=art.title,
                        decision=ScreeningDecisionType.INCLUDE,
                        reason=f"Auto-included (error: {str(e)[:50]})",
                        stage=ScreeningStage.TITLE_ABSTRACT,
                    ))

        flow.excluded_title_abstract = len(ta_excluded)
        up(f"Screening: {len(ta_included)} included, {len(ta_excluded)} excluded")

        if not ta_included:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto,
                search_queries=all_search_queries,
                flow=flow,
                screening_log=all_screening,
                synthesis_text="All articles excluded during title/abstract screening.",
                timestamp=datetime.now().isoformat(),
            )

        # 8. Full-text retrieval
        flow.sought_fulltext = len(ta_included)
        pmc_articles = [a for a in ta_included if a.pmc_id and not a.full_text]
        if pmc_articles:
            up(f"Fetching full text for {len(pmc_articles)} PMC articles...")
            texts = self.pubmed.fetch_full_text([a.pmc_id for a in pmc_articles])
            for a in pmc_articles:
                if a.pmc_id in texts:
                    a.full_text = texts[a.pmc_id]
            up(f"  Retrieved {len(texts)} full texts")

        flow.not_retrieved = len(ta_included) - len([a for a in ta_included if a.full_text or a.abstract])

        # 9. Full-text eligibility screening
        ft_articles = [a for a in ta_included if a.full_text]
        no_ft = [a for a in ta_included if not a.full_text]
        ft_included = list(no_ft)
        ft_excluded: list[Article] = []

        if ft_articles:
            up(f"Full-text eligibility screening ({len(ft_articles)} articles)...")
            for batch_start in range(0, len(ft_articles), 10):
                batch = ft_articles[batch_start:batch_start + 10]
                try:
                    batch_result = await run_screening(batch, self.deps, "full_text")
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
                                art.inclusion_status = InclusionStatus.EXCLUDED
                                art.exclusion_reason = dec.reason
                                ft_excluded.append(art)
                except Exception as e:
                    up(f"  FT screening batch failed ({e}), auto-including")
                    ft_included.extend(batch)

        flow.assessed_eligibility = len(ta_included)
        flow.excluded_eligibility = len(ft_excluded)
        flow.included_synthesis = len(ft_included)

        reason_counts: dict[str, int] = defaultdict(int)
        for a in ta_excluded + ft_excluded:
            r = a.exclusion_reason[:60] if a.exclusion_reason else "Unspecified"
            reason_counts[r] += 1
        flow.excluded_reasons = dict(sorted(reason_counts.items(), key=lambda x: -x[1])[:8])

        up(f"Final included: {flow.included_synthesis} articles")

        if not ft_included:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto,
                search_queries=all_search_queries,
                flow=flow,
                screening_log=all_screening,
                synthesis_text="All articles excluded during eligibility assessment.",
                timestamp=datetime.now().isoformat(),
            )

        # 10. Evidence span extraction
        up("Extracting evidence spans...")
        evidence = await extract_evidence(ft_included, self.deps)
        up(f"Extracted {len(evidence)} evidence spans")

        # 11. Per-study data extraction
        if data_items:
            up(f"Extracting data from {len(ft_included)} studies...")
            for i, art in enumerate(ft_included):
                up(f"  [{i+1}/{len(ft_included)}] {art.title[:50]}...")
                try:
                    art.extracted_data = await run_data_extraction(art, data_items, self.deps)
                except Exception as e:
                    up(f"  Data extraction failed for {art.pmid}: {e}")

        # 12. Risk of bias
        up(f"Assessing risk of bias ({proto.rob_tool.value})...")
        for i, art in enumerate(ft_included):
            try:
                art.risk_of_bias = await run_risk_of_bias(art, self.deps)
            except Exception as e:
                up(f"  RoB failed for {art.pmid}: {e}")

        # 13. Synthesis
        flow_text = (
            f"Identified: {flow.total_identified} | After dedup: {flow.after_dedup} | "
            f"Screened: {flow.screened_title_abstract} | Excluded (TA): {flow.excluded_title_abstract} | "
            f"FT assessed: {flow.assessed_eligibility} | Excluded (FT): {flow.excluded_eligibility} | "
            f"Included: {flow.included_synthesis}"
        )
        up(f"Synthesizing {len(ft_included)} articles...")
        synthesis = await run_synthesis(ft_included, evidence, flow_text, self.deps)

        # 14. Bias, GRADE, limitations
        up("Assessing overall bias and GRADE...")
        outcome_text = proto.pico_outcome or "Primary outcome"
        outcomes = [o.strip() for o in outcome_text.split(",") if o.strip()]
        grade_tasks = {outcome: run_grade(outcome, ft_included, self.deps) for outcome in outcomes[:3]}
        bias_result, limitations_result, *grade_results = await asyncio.gather(
            run_bias_summary(ft_included, self.deps),
            run_limitations(flow_text, ft_included, self.deps),
            *grade_tasks.values(),
            return_exceptions=True,
        )
        bias_text = bias_result if isinstance(bias_result, str) else ""
        limitations_text = limitations_result if isinstance(limitations_result, str) else ""
        grade_assessments: dict[str, GRADEAssessment] = {}
        for outcome, result in zip(grade_tasks.keys(), grade_results):
            if isinstance(result, GRADEAssessment):
                grade_assessments[outcome] = result

        # 15. Charting, appraisal, narrative, grounding, document sections
        charting_questions = list(proto.charting_questions) if proto.charting_questions else None
        charting_template = proto.charting_template or default_charting_template()
        appraisal_config = proto.critical_appraisal_config or default_appraisal_config()
        data_charting_rubrics = []
        critical_appraisals = []
        critical_appraisal_results: list[CriticalAppraisalResult] = []
        narrative_rows = []
        _resolved_cfg = _resolve_section_config(proto)

        for article in ft_included:
            try:
                up(f"Charting article {article.pmid}…")
                rubric = await run_data_charting(
                    article, self.deps, charting_questions,
                    resolved_section_config=_resolved_cfg,
                    charting_template=charting_template,
                )
                data_charting_rubrics.append(rubric)
                appraisal_rubric, appraisal_result = await run_critical_appraisal(
                    article, rubric, self.deps, None, appraisal_config=appraisal_config,
                )
                critical_appraisals.append(appraisal_rubric)
                critical_appraisal_results.append(appraisal_result)
                row = await run_narrative_row(rubric, appraisal_rubric, self.deps)
                narrative_rows.append(row)
            except Exception as exc:
                logger.warning("Charting/appraisal failed for %s: %s", article.pmid, exc)

        grounding_validation = None
        try:
            up("Validating grounding of synthesis text…")
            corpus = {a.pmid: (a.abstract or "") + " " + (a.full_text or "") for a in ft_included}
            citation_map = {a.pmid: f"{a.authors} ({a.year})" for a in ft_included}
            grounding_validation = await run_grounding_validation(synthesis, corpus, citation_map, self.deps)
        except Exception as exc:
            logger.warning("Grounding validation failed: %s", exc)

        grade_summary = "; ".join(f"{k}: {v.overall_certainty.value}" for k, v in grade_assessments.items())
        flow_text_short = f"{flow.total_identified} identified, {flow.included_synthesis} included"
        try:
            up("Generating document sections…")
            introduction_text, conclusions_text, structured_abstract = await asyncio.gather(
                run_introduction(self.deps),
                run_conclusions(synthesis, grade_summary, self.deps),
                run_abstract(flow_text_short, synthesis, self.deps),
            )
        except Exception as exc:
            logger.warning("Document section generation failed: %s", exc)
            introduction_text = conclusions_text = structured_abstract = ""

        up("Review complete!")
        final_result = PRISMAReviewResult(
            research_question=proto.question,
            protocol=proto,
            search_queries=all_search_queries,
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

        if final_result.included_articles:
            try:
                up("Assembling structured PrismaReview report...")
                final_result.prisma_review = await assemble_prisma_review(
                    final_result, self.deps, output_synthesis_style,
                    resolved_config=_resolved_cfg,
                    progress_callback=up,
                    assemble_timeout=assemble_timeout,
                )
                pr = final_result.prisma_review
                final_result.synthesis_text = final_result.synthesis_text or "\n\n".join(
                    t.description for t in pr.results.themes
                )
                final_result.structured_abstract = final_result.structured_abstract or (
                    f"Background: {pr.abstract.background}\nObjective: {pr.abstract.objective}\n"
                    f"Methods: {pr.abstract.methods}\nResults: {pr.abstract.results}\n"
                    f"Conclusion: {pr.abstract.conclusion}"
                )
                up(f"PrismaReview assembled: {len(pr.results.themes)} themes, "
                   f"{len(pr.results.extracted_studies or [])} studies")
            except Exception as exc:
                logger.warning("PrismaReview assembly failed: %s", exc)

        return final_result

    async def run_compare(
        self,
        models: list[str],
        *,
        progress_callback: Optional[Callable[[str], None]] = None,
        data_items: Optional[list[str]] = None,
        auto_confirm: bool = True,
        confirm_callback: Optional[Callable[[ReviewPlan], "bool | str"]] = None,
        max_plan_iterations: int = 3,
        consensus_model: Optional[str] = None,
        output_synthesis_style: str = "paragraph",
        assemble_timeout: float = 3600.0,
    ) -> CompareReviewResult:
        """Run the review pipeline in parallel for each model in *models*.

        Article acquisition (Steps 1–6) runs once and is shared. All LLM steps
        (7–15) run independently per model via asyncio.gather. Returns a
        CompareReviewResult with one ModelReviewRun per model and a
        MergedReviewResult with field-level agreement and consensus synthesis.
        """
        from .compare import run_compare as _compare
        return await _compare(
            self,
            models,
            progress_callback=progress_callback,
            data_items=data_items,
            auto_confirm=auto_confirm,
            confirm_callback=confirm_callback,
            max_plan_iterations=max_plan_iterations,
            consensus_model=consensus_model,
            output_synthesis_style=output_synthesis_style,
            assemble_timeout=assemble_timeout,
        )


# Section name → field names mapping for DataExtractionSchema assembly
_CHARTING_SECTIONS: list[tuple[str, list[str]]] = [
    ("Publication Information", [
        "title", "authors", "year", "journal_conference", "doi",
        "database_retrieved", "disorder_cohort", "primary_focus",
    ]),
    ("Study Design", [
        "primary_goal", "study_design", "duration_frequency",
        "subject_model", "task_type", "study_setting", "country_region",
    ]),
    ("Participants: Disordered Group", [
        "disorder_diagnosis", "diagnosis_assessment", "n_disordered",
        "age_mean_sd", "age_range", "gender_distribution",
        "comorbidities_included_excluded", "medications_therapies", "severity_levels",
    ]),
    ("Participants: Healthy Controls", [
        "healthy_controls_included", "healthy_status_confirmed", "n_controls",
        "age_mean_sd_controls", "age_range_controls", "gender_distribution_controls",
        "age_matched", "gender_matched", "neurodevelopmentally_typical",
    ]),
    ("Data Collection", [
        "data_types", "tasks_performed", "equipment_tools",
        "new_dataset_contributed", "dataset_openly_available",
        "dataset_available_request", "sensitive_data_anonymized",
    ]),
    ("Features and Models", [
        "feature_types", "specific_features", "feature_extraction_tools",
        "feature_importance_reported", "importance_method", "top_features_identified",
        "feature_change_direction", "model_category", "specific_algorithms",
        "validation_methodology", "performance_metrics", "key_performance_results",
    ]),
    ("Synthesis", [
        "summary_key_findings", "features_associated_disorder",
        "future_directions_recommended", "reviewer_notes",
    ]),
]


def _resolve_section_config(protocol: ReviewProtocol) -> list[tuple[str, str, str]]:
    """Return [(section_key, display_title, format_type)] ordered by display order.

    Precedence: rubric_section_config > section_output_formats > built-in defaults.
    Custom charting_questions not covered by config are appended at the end.
    """
    import warnings

    _valid_fmts = {"descriptive", "yes_no", "table", "bullet_list", "numeric"}

    # Start with built-in sections at default order
    entries: list[dict] = [
        {"key": k, "title": t, "format": "descriptive", "order": i}
        for i, (k, t) in enumerate(BUILTIN_SECTIONS)
    ]

    # Apply section_output_formats overrides (format only, title/order unchanged)
    for fmt_title, fmt_type in protocol.section_output_formats.items():
        matched = False
        for entry in entries:
            if entry["title"].lower() == fmt_title.lower() or entry["key"] == fmt_title:
                entry["format"] = fmt_type
                matched = True
                break
        if not matched:
            warnings.warn(
                f"section_output_formats key '{fmt_title}' does not match any built-in section; ignoring",
                UserWarning,
                stacklevel=2,
            )

    # Apply rubric_section_config overrides (title, order, and format)
    builtin_keys = {e["key"] for e in entries}
    for cfg in protocol.rubric_section_config:
        if cfg.section_key in builtin_keys:
            for entry in entries:
                if entry["key"] == cfg.section_key:
                    entry["title"] = cfg.section_name
                    entry["order"] = cfg.order
                    entry["format"] = cfg.output_format
                    break
        else:
            entries.append({
                "key": cfg.section_key,
                "title": cfg.section_name,
                "format": cfg.output_format,
                "order": cfg.order,
            })

    # Append custom charting_questions not already covered
    covered_titles = {e["title"].lower() for e in entries}
    covered_keys = {e["key"] for e in entries}
    for i, question in enumerate(protocol.charting_questions):
        q_key = question[:20]
        if question.lower() not in covered_titles and q_key not in covered_keys:
            entries.append({"key": q_key, "title": question, "format": "descriptive", "order": 100 + i})

    entries.sort(key=lambda e: e["order"])
    return [(e["key"], e["title"], e["format"]) for e in entries]


def _assemble_methods(
    protocol: ReviewProtocol,
    search_queries: list[str],
    flow_counts: PRISMAFlowCounts,
    charting_rubrics: list,
    bias_assessment: str,
    resolved_config: list[tuple[str, str, str]] | None = None,
    appraisal_results: list[CriticalAppraisalResult] | None = None,
) -> Methods:
    """Build Methods deterministically from existing pipeline data — no LLM call."""
    prisma_flow = PrismaFlow(
        total_identified=flow_counts.total_identified,
        duplicates_removed=flow_counts.duplicates_removed,
        screened=flow_counts.screened_title_abstract,
        excluded=flow_counts.excluded_title_abstract,
        full_text_reviewed=flow_counts.assessed_eligibility,
        final_included=flow_counts.included_synthesis,
    )

    inclusion_list = [c.strip() for c in protocol.inclusion_criteria.split("\n") if c.strip()]
    exclusion_list = [c.strip() for c in protocol.exclusion_criteria.split("\n") if c.strip()]

    query_str = "; ".join(search_queries[:5]) if search_queries else "Not recorded"
    search_strategy = (
        f"Systematic searches were conducted across {', '.join(protocol.databases)}. "
        f"Queries: {query_str}"
    )

    extraction_schemas = [
        DataExtractionSchema(
            section_name=section_name,
            fields=[
                DataExtractionField(
                    field_name=f,
                    description=f.replace("_", " ").title(),
                )
                for f in fields
            ],
        )
        for section_name, fields in _CHARTING_SECTIONS
    ]

    # Resolve charting template for field_answers assembly (Feature 006)
    charting_template = protocol.charting_template or default_charting_template()

    # Build data_extraction — sections from US5, field_answers from Feature 006
    data_extraction: list[StudyDataExtractionReport] = []
    if resolved_config:
        for rubric in charting_rubrics:
            sections_ordered = {
                title: rubric.section_outputs[title]
                for _, title, _ in resolved_config
                if title in rubric.section_outputs
            }

            # Assemble field_answers from charting_template + rubric field values
            field_answers: dict[str, SectionExtractionResult] = {}
            for section in charting_template.sections:
                extractable = [f for f in section.fields if not f.reviewer_only]
                if not extractable:
                    continue
                fa_list: list[FieldAnswer] = []
                for field_def in extractable:
                    # Best-effort: look up value by field_name mapped to rubric attribute
                    rubric_attr = field_def.field_name.lower().replace(" ", "_").replace("/", "_").replace("—", "_").replace("-", "_")
                    rubric_attr = "".join(c if c.isalnum() or c == "_" else "_" for c in rubric_attr).strip("_")
                    raw_value = getattr(rubric, rubric_attr, None)
                    if raw_value is None:
                        # Try section-prefixed lookup via _SECTION_KEY_FIELDS mapping
                        from .agents import _SECTION_KEY_FIELDS as _skf
                        section_fields = _skf.get(section.section_key, [])
                        if section_fields:
                            idx = extractable.index(field_def)
                            if idx < len(section_fields):
                                raw_value = getattr(rubric, section_fields[idx], None)
                    value = str(raw_value) if raw_value is not None else None
                    if value:
                        fa_list.append(FieldAnswer(field_name=field_def.field_name, value=value, confidence="medium"))
                    else:
                        fa_list.append(FieldAnswer(
                            field_name=field_def.field_name,
                            value=None,
                            confidence="low",
                            extraction_note="Value not found in rubric",
                        ))
                if fa_list:
                    field_answers[section.section_key] = SectionExtractionResult(
                        section_key=section.section_key,
                        section_title=section.section_title,
                        field_answers=fa_list,
                    )

            data_extraction.append(
                StudyDataExtractionReport(
                    source_id=rubric.source_id,
                    sections=sections_ordered,
                    field_answers=field_answers,
                )
            )

    return Methods(
        search_strategy=search_strategy,
        study_selection=prisma_flow,
        inclusion_criteria=inclusion_list or [protocol.inclusion_criteria],
        exclusion_criteria=exclusion_list or [protocol.exclusion_criteria],
        data_extraction_schema=extraction_schemas,
        data_extraction=data_extraction,
        quality_assessment=bias_assessment or "Quality assessment completed.",
        critical_appraisal_results=appraisal_results or [],
    )


def _assemble_extracted_studies(charting_rubrics: list) -> list[ExtractedStudy]:
    """Build ExtractedStudy list from DataChartingRubric Sections A+B — no LLM call."""
    studies = []
    for rubric in charting_rubrics:
        try:
            year_val = int(rubric.year) if rubric.year and rubric.year.isdigit() else 0
            metadata = SourceMetadata(
                source_id=rubric.source_id,
                title=rubric.title or "Unknown",
                authors=rubric.authors or "Unknown",
                year=year_val,
                journal_or_conference=rubric.journal_conference or None,
                doi=rubric.doi or None,
                database_retrieved_from=rubric.database_retrieved or "unknown",
                disorder_cohort=rubric.disorder_cohort or None,
                primary_focus=rubric.primary_focus or None,
            )
            design = StudyDesign(
                primary_study_goal=rubric.primary_goal or "Not specified",
                study_design=rubric.study_design or "unclear",
                longitudinal_duration=rubric.duration_frequency or None,
                subject_model=rubric.subject_model or None,
                task_type=rubric.task_type or None,
                study_setting=rubric.study_setting or None,
                country_or_region=rubric.country_region or None,
            )
            studies.append(ExtractedStudy(metadata=metadata, design=design))
        except Exception as exc:
            logger.warning("ExtractedStudy assembly failed for rubric %s: %s", getattr(rubric, "source_id", "?"), exc)
    return studies


async def assemble_prisma_review(
    result: "PRISMAReviewResult",
    deps: "AgentDeps",
    output_style: str = "paragraph",
    resolved_config: list[tuple[str, str, str]] | None = None,
    assemble_timeout: float = 3600.0,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> PrismaReview:
    """Orchestrate 5 agents and 2 deterministic helpers to build a PrismaReview."""
    cb = progress_callback or (lambda _: None)
    protocol = result.protocol

    # Step 1: Deterministic assembly
    methods = _assemble_methods(
        protocol=protocol,
        search_queries=result.search_queries,
        flow_counts=result.flow,
        charting_rubrics=result.data_charting_rubrics,
        bias_assessment=result.bias_assessment,
        resolved_config=resolved_config,
        appraisal_results=result.structured_appraisal_results or None,
    )
    extracted_studies = _assemble_extracted_studies(result.data_charting_rubrics)

    # Step 2: Wave 1 — parallel LLM calls (thematic synthesis, introduction, quantitative analysis)
    cb("Assembly: synthesising themes, introduction, and quantitative analysis...")
    try:
        wave1_results = await asyncio.wait_for(
            asyncio.gather(
                run_thematic_synthesis(
                    deps, result.included_articles, result.evidence_spans,
                    result.data_charting_rubrics, output_style,
                ),
                run_introduction_section(deps, protocol),
                run_quantitative_analysis(deps, result.included_articles),
                return_exceptions=True,
            ),
            timeout=assemble_timeout,
        )
    except asyncio.TimeoutError:
        logger.error("Wave 1 assembly timed out after %.0f s", assemble_timeout)
        raise
    synthesis_result, introduction, quant_analysis = wave1_results

    if isinstance(synthesis_result, BaseException):
        raise synthesis_result
    if isinstance(introduction, BaseException):
        logger.warning("Introduction section generation failed: %s", introduction)
        introduction = Introduction(
            background=protocol.objective,
            problem_statement="",
            research_gap="",
            objectives=protocol.objective,
        )
    if isinstance(quant_analysis, BaseException):
        logger.warning("Quantitative analysis failed: %s", quant_analysis)
        quant_analysis = None

    # Step 3: Wave 2 — parallel LLM calls requiring themes from Wave 1
    cb("Assembly: generating abstract, discussion, and conclusions...")
    try:
        wave2_results = await asyncio.wait_for(
            asyncio.gather(
                run_abstract_section(
                    deps, protocol, synthesis_result.themes,
                    methods.study_selection, synthesis_result.bias_assessment.overall_quality,
                ),
                run_discussion_section(deps, protocol, synthesis_result.themes, result.limitations),
                run_conclusion_section(deps, protocol, synthesis_result.themes),
                return_exceptions=True,
            ),
            timeout=assemble_timeout,
        )
    except asyncio.TimeoutError:
        logger.error("Wave 2 assembly timed out after %.0f s", assemble_timeout)
        raise
    abstract_result, discussion, conclusion = wave2_results

    if isinstance(abstract_result, BaseException):
        logger.warning("Abstract section generation failed: %s", abstract_result)
        abstract_result = Abstract(
            background="", objective=protocol.objective, methods="", results="", conclusion="",
        )
    if isinstance(discussion, BaseException):
        logger.warning("Discussion section generation failed: %s", discussion)
        discussion = Discussion(
            summary_of_findings="",
            interpretation="",
            comparison_with_literature="",
            implications=Implications(clinical="", policy="", research=""),
            limitations=result.limitations,
        )
    if isinstance(conclusion, BaseException):
        logger.warning("Conclusion section generation failed: %s", conclusion)
        conclusion = Conclusion(key_takeaways="", recommendations="", future_research="")

    # Step 4: Build references
    cb("Assembly: building references and validating study IDs...")
    references = [a.citation for a in result.included_articles]

    # Step 5: Validate — T014 validation logging
    if len(extracted_studies) != methods.study_selection.final_included:
        logger.warning(
            "extracted_studies count (%d) != final_included (%d)",
            len(extracted_studies),
            methods.study_selection.final_included,
        )

    extracted_ids = {s.metadata.source_id for s in extracted_studies}
    for theme in synthesis_result.themes:
        for sid in theme.supporting_studies:
            if sid not in extracted_ids:
                logger.warning("Orphaned supporting_study ID: %s", sid)

    # Step 6: Return PrismaReview
    return PrismaReview(
        title=result.research_question,
        abstract=abstract_result,
        introduction=introduction,
        methods=methods,
        results=Results(
            output_format=OutputFormat(style=output_style),
            prisma_flow_summary=methods.study_selection,
            extracted_studies=extracted_studies,
            paragraph_summary=synthesis_result.paragraph_summary,
            question_answer_summary=synthesis_result.question_answer_summary,
            themes=synthesis_result.themes,
            quantitative_analysis=quant_analysis,
            bias_assessment=synthesis_result.bias_assessment,
        ),
        discussion=discussion,
        conclusion=conclusion,
        references=references,
        optional=OptionalSection(),
    )


def _build_review_plan(strategy, question: str, iteration: int) -> ReviewPlan:
    """Build a ReviewPlan from a SearchStrategy for presentation to the user."""
    return ReviewPlan(
        research_question=question,
        pubmed_queries=strategy.pubmed_queries,
        biorxiv_queries=strategy.biorxiv_queries,
        mesh_terms=strategy.mesh_terms,
        key_concepts=strategy.key_concepts,
        rationale=strategy.rationale,
        iteration=iteration,
    )
