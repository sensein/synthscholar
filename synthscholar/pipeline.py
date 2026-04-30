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
from typing import Callable, NamedTuple, Optional

import logging

from .models import (
    Article,
    InclusionStatus,
    ReviewProtocol,
    PRISMAFlowCounts,
    PRISMAReviewResult,
    # Feature 007
    CompareReviewResult,
    ModelReviewRun,
    MergedReviewResult,
    FieldAgreement,
    SynthesisDivergence,
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
    PlanIteration,
    SearchIteration,
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
    DataChartingRubric,
    CriticalAppraisalRubric,
    PRISMANarrativeRow,
    GroundingValidationResult,
    RiskOfBiasResult,
)
from .clients import (
    Cache,
    PubMedClient,
    BioRxivClient,
    MedRxivClient,
    FullTextResolver,
)
from .evidence import extract_evidence
from .provenance import (
    ProvenanceCollector,
    build_run_configuration,
    content_sha256,
)
from .agents import (
    AgentDeps,
    build_model,
    run_search_strategy,
    run_screening,
    run_risk_of_bias,
    run_data_extraction,
    run_synthesis,
    run_synthesis_merge_agent,
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
    from .cache.models import (
        CacheUnavailableError,
        CacheSchemaError,
        PipelineCheckpoint,
        BatchMaxRetriesError,
    )
    _CACHE_AVAILABLE = True
except ImportError:
    _CACHE_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Stage-name constants ──────────────────────────────────────────────────────
STAGE_TITLE_ABSTRACT  = "title_abstract_screening"
STAGE_FULL_TEXT       = "full_text_eligibility"
STAGE_EXTRACTION      = "evidence_extraction"
STAGE_CHARTING        = "data_charting"
STAGE_ROB             = "risk_of_bias"
STAGE_APPRAISAL       = "critical_appraisal"
STAGE_NARRATIVE       = "narrative_synthesis"
STAGE_SYNTHESIS       = "synthesis"
STAGE_SYNTHESIS_MERGE = "synthesis_merge"
STAGE_ASSEMBLY        = "assembly"


# ── DB checkpoint helpers ─────────────────────────────────────────────────────

async def _load_or_run_batch(
    store: "CacheStore | None",
    review_id: str,
    stage: str,
    batch_index: int,
    run_fn: "Callable[[], Any]",
    max_retries: int = 3,
) -> dict:
    """Load a completed batch from DB or execute and persist it.

    When store is None (pg_dsn not configured) calls run_fn directly and
    returns the result without touching the database — zero behaviour change
    for callers without PostgreSQL configured.

    Raises BatchMaxRetriesError when retries are exhausted.
    """
    import asyncio as _asyncio

    if store is None:
        result = run_fn()
        if _asyncio.iscoroutine(result):
            result = await result
        return result if isinstance(result, dict) else {"value": result}

    existing = await store.load_checkpoint(review_id, stage, batch_index)
    if existing and existing.status == "complete":
        return existing.result_json
    if existing and existing.status == "failed" and existing.retries >= max_retries:
        raise BatchMaxRetriesError(stage, batch_index, existing.retries)

    retries = existing.retries if existing else 0
    ckpt = PipelineCheckpoint(
        review_id=review_id,
        stage_name=stage,
        batch_index=batch_index,
        status="in_progress",
        retries=retries,
    )
    ckpt = await store.save_checkpoint(ckpt)
    logger.info(
        "batch_start stage=%s batch=%d review=%s", stage, batch_index, review_id
    )

    import time
    t0 = time.monotonic()
    try:
        result = run_fn()
        if _asyncio.iscoroutine(result):
            result = await result
        payload = result if isinstance(result, dict) else {"value": result}
        ckpt.status = "complete"
        ckpt.result_json = payload
        ckpt.error_message = ""
        await store.save_checkpoint(ckpt)
        logger.info(
            "batch_complete stage=%s batch=%d elapsed=%.1fs",
            stage, batch_index, time.monotonic() - t0,
        )
        return payload
    except Exception as exc:
        ckpt.retries += 1
        ckpt.status = "failed"
        ckpt.error_message = str(exc)[:500]
        await store.save_checkpoint(ckpt)
        logger.warning(
            "batch_failed stage=%s batch=%d attempt=%d error=%s",
            stage, batch_index, ckpt.retries, ckpt.error_message,
        )
        raise


async def _parallel_ta_screening(
    articles: list[Article],
    deps: "AgentDeps",
    concurrency: int,
    up: "Callable[[str], None]",
) -> tuple[list[Article], list[Article], list[ScreeningLogEntry]]:
    """Run title/abstract screening on *articles* with *concurrency* parallel batches.

    Returns (included, excluded, screening_log).
    """
    batches = [articles[i:i + 15] for i in range(0, len(articles), 15)]
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[list[Article], list[ScreeningLogEntry], list[Article]]] = [None] * len(batches)  # type: ignore[list-item]

    async def _run(bidx: int, batch: list[Article]) -> None:
        async with sem:
            b_inc: list[Article] = []
            b_exc: list[Article] = []
            b_log: list[ScreeningLogEntry] = []
            try:
                batch_result = await run_screening(batch, deps, "title_abstract")
                for dec in batch_result.decisions:
                    if 0 <= dec.index < len(batch):
                        art = batch[dec.index]
                        art.quality_score = dec.relevance_score
                        b_log.append(ScreeningLogEntry(
                            pmid=art.pmid, title=art.title,
                            decision=dec.decision, reason=dec.reason,
                            stage=ScreeningStage.TITLE_ABSTRACT,
                        ))
                        if dec.decision == ScreeningDecisionType.INCLUDE:
                            art.inclusion_status = InclusionStatus.INCLUDED
                            b_inc.append(art)
                        else:
                            art.inclusion_status = InclusionStatus.EXCLUDED
                            art.exclusion_reason = dec.reason
                            b_exc.append(art)
                covered = {e.pmid for e in b_log}
                for art in batch:
                    if art.pmid not in covered:
                        b_inc.append(art)
                        b_log.append(ScreeningLogEntry(
                            pmid=art.pmid, title=art.title,
                            decision=ScreeningDecisionType.INCLUDE,
                            reason="Not evaluated — auto-included",
                            stage=ScreeningStage.TITLE_ABSTRACT,
                        ))
            except Exception as e:
                up(f"  Screening batch {bidx} failed ({e}), auto-including {len(batch)}")
                for art in batch:
                    b_inc.append(art)
                    b_log.append(ScreeningLogEntry(
                        pmid=art.pmid, title=art.title,
                        decision=ScreeningDecisionType.INCLUDE,
                        reason=f"Auto-included (error: {str(e)[:50]})",
                        stage=ScreeningStage.TITLE_ABSTRACT,
                    ))
            results[bidx] = (b_inc, b_log, b_exc)

    await asyncio.gather(*[_run(i, b) for i, b in enumerate(batches)])

    included: list[Article] = []
    excluded: list[Article] = []
    log: list[ScreeningLogEntry] = []
    for b_inc, b_log, b_exc in results:
        if b_inc is None:
            continue
        included.extend(b_inc)
        excluded.extend(b_exc)
        log.extend(b_log)
    return included, excluded, log


async def _parallel_ft_screening(
    articles: list[Article],
    deps: "AgentDeps",
    concurrency: int,
    up: "Callable[[str], None]",
) -> tuple[list[Article], list[Article], list[ScreeningLogEntry]]:
    """Run full-text eligibility screening on *articles* with *concurrency* parallel batches.

    Returns (included, excluded, screening_log).
    """
    batches = [articles[i:i + 10] for i in range(0, len(articles), 10)]
    sem = asyncio.Semaphore(concurrency)
    results: list[tuple[list[Article], list[ScreeningLogEntry], list[Article]]] = [None] * len(batches)  # type: ignore[list-item]

    async def _run(bidx: int, batch: list[Article]) -> None:
        async with sem:
            b_inc: list[Article] = []
            b_exc: list[Article] = []
            b_log: list[ScreeningLogEntry] = []
            try:
                batch_result = await run_screening(batch, deps, "full_text")
                for dec in batch_result.decisions:
                    if 0 <= dec.index < len(batch):
                        art = batch[dec.index]
                        b_log.append(ScreeningLogEntry(
                            pmid=art.pmid, title=art.title,
                            decision=dec.decision, reason=dec.reason,
                            stage=ScreeningStage.FULL_TEXT,
                        ))
                        if dec.decision == ScreeningDecisionType.INCLUDE:
                            b_inc.append(art)
                        else:
                            art.inclusion_status = InclusionStatus.EXCLUDED
                            art.exclusion_reason = dec.reason
                            b_exc.append(art)
            except Exception as e:
                up(f"  FT screening batch {bidx} failed ({e}), auto-including")
                b_inc.extend(batch)
            results[bidx] = (b_inc, b_log, b_exc)

    await asyncio.gather(*[_run(i, b) for i, b in enumerate(batches)])

    included: list[Article] = []
    excluded: list[Article] = []
    log: list[ScreeningLogEntry] = []
    for b_inc, b_log, b_exc in results:
        if b_inc is None:
            continue
        included.extend(b_inc)
        excluded.extend(b_exc)
        log.extend(b_log)
    return included, excluded, log


@dataclass
class AcquisitionResult:
    """Output of the shared article-acquisition phase (Steps 1–6)."""
    deduped: list[Article]
    all_search_queries: list[str]
    flow: PRISMAFlowCounts


# Sentinel sources understood by the per-database tally; everything else
# (e.g. OpenAlex, Europe PMC) lands in ``db_other_sources``.
_KNOWN_DB_SOURCES = {"pubmed_search", "biorxiv", "medrxiv"}


def _apply_per_db_tally(flow: PRISMAFlowCounts, all_articles: dict[str, Article]) -> None:
    """Recompute every per-database PRISMA count from ``Article.source`` values.

    Mutates ``flow`` in place: sets ``db_pubmed``, ``db_biorxiv``,
    ``db_medrxiv``, ``db_related``, ``db_hops``, and ``db_other_sources``.
    Idempotent — safe to call multiple times during discovery; each call
    fully replaces previous tallies.
    """
    counts = {"db_pubmed": 0, "db_biorxiv": 0, "db_medrxiv": 0,
              "db_related": 0, "db_hops": 0}
    other: dict[str, int] = {}
    for a in all_articles.values():
        s = a.source or ""
        if s == "pubmed_search":
            counts["db_pubmed"] += 1
        elif s == "biorxiv":
            counts["db_biorxiv"] += 1
        elif s == "medrxiv":
            counts["db_medrxiv"] += 1
        elif s.startswith("related_"):
            counts["db_related"] += 1
        elif s.startswith("hop_"):
            counts["db_hops"] += 1
        elif s:
            other[s] = other.get(s, 0) + 1
    flow.db_pubmed = counts["db_pubmed"]
    flow.db_biorxiv = counts["db_biorxiv"]
    flow.db_medrxiv = counts["db_medrxiv"]
    flow.db_related = counts["db_related"]
    flow.db_hops = counts["db_hops"]
    flow.db_other_sources = other


def _rerank_articles(articles: list[Article], question: str, max_n: int) -> list[Article]:
    """Return the top *max_n* articles ranked by heuristic relevance to *question*.

    Scoring (all normalised to 0-1, then weighted):
      - Title keyword overlap with question tokens  (weight 3)
      - Abstract keyword overlap                    (weight 2)
      - MeSH/keyword overlap                        (weight 1)
      - Source bonus: primary search > related > hop (weight 0.5)
    No LLM calls — runs in microseconds per article.
    """
    import re

    stop = {
        "a", "an", "the", "of", "in", "on", "at", "to", "for", "and", "or",
        "is", "are", "was", "were", "with", "by", "from", "as", "that", "this",
        "it", "its", "be", "been", "has", "have", "had", "do", "does", "did",
        "not", "no", "but", "if", "so", "we", "our", "their", "which", "who",
    }

    def tokens(text: str) -> set[str]:
        return {w for w in re.findall(r"[a-z]+", text.lower()) if w not in stop and len(w) > 2}

    q_tokens = tokens(question)
    if not q_tokens:
        return articles[:max_n]

    source_bonus = {"pubmed_search": 1.0, "biorxiv": 0.9}

    def score(a: Article) -> float:
        t = tokens(a.title)
        ab = tokens(a.abstract[:500])
        mesh = tokens(" ".join(a.mesh_terms + a.keywords))

        title_overlap = len(t & q_tokens) / len(q_tokens) if q_tokens else 0
        abstract_overlap = len(ab & q_tokens) / len(q_tokens) if q_tokens else 0
        mesh_overlap = len(mesh & q_tokens) / len(q_tokens) if q_tokens else 0

        src = source_bonus.get(a.source, 0.7) if not a.source.startswith("hop") else 0.5

        return 3 * title_overlap + 2 * abstract_overlap + mesh_overlap + 0.5 * src

    ranked = sorted(articles, key=score, reverse=True)
    return ranked[:max_n]


class PRISMAReviewPipeline:
    """Full PRISMA 2020 pipeline with async agent orchestration."""

    def __init__(
        self,
        api_key: str,
        model_name: str = "anthropic/claude-sonnet-4",
        ncbi_api_key: str = "",
        email: str = "",
        api_keys: Optional[dict[str, str]] = None,
        protocol: Optional[ReviewProtocol] = None,
        enable_cache: bool = True,
        max_per_query: int = 20,
        related_depth: int = 1,
        biorxiv_days: int = 180,
    ):
        # `email` and entries in `api_keys` fall back to environment variables
        # inside FullTextResolver. Specifically:
        #   - email      → SYNTHSCHOLAR_EMAIL env var, then NCBI_EMAIL default
        #   - api_keys   → SEMANTIC_SCHOLAR_API_KEY / CORE_API_KEY env vars
        # Explicit constructor arguments always win over the env vars.
        self.cache = Cache() if enable_cache else None
        self.pubmed = PubMedClient(api_key=ncbi_api_key, cache=self.cache)
        self.biorxiv = BioRxivClient(self.cache)
        self.medrxiv = MedRxivClient(self.cache)
        self.full_text_resolver = FullTextResolver(
            email=email, api_keys=api_keys, cache=self.cache,
        )
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
        self.deps.model = build_model(self.deps.api_key, self.deps.model_name)
        self._log: list[str] = []

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {msg}"
        self._log.append(entry)
        print(entry)

    async def _resolve_plan_with_iterations(
        self,
        strategy,
        provenance: "ProvenanceCollector",
        confirm_callback,
        max_plan_iterations: int,
        effective_auto: bool,
        up,
    ):
        """Run the plan-confirmation loop, recording every iteration on `provenance`.

        Returns the final approved (or auto-confirmed) strategy. Raises
        :class:`PlanRejectedError` / :class:`MaxIterationsReachedError`
        with iteration history already captured.
        """
        from datetime import timezone as _tz
        proto = self.protocol
        if not effective_auto and confirm_callback is not None:
            for iteration in range(1, max_plan_iterations + 1):
                plan = _build_review_plan(strategy, proto.question, iteration=iteration)
                up(f"Awaiting plan confirmation (iteration {iteration})...")
                response = confirm_callback(plan)
                gen_at = datetime.now(_tz.utc).isoformat()
                if response is True or response == "":
                    provenance.record_plan_iteration(PlanIteration(
                        iteration_index=iteration, plan_snapshot=plan,
                        decision="approved", generated_at=gen_at,
                        model_name=self.model_name,
                    ))
                    up("Plan approved.")
                    return strategy
                if response is False:
                    provenance.record_plan_iteration(PlanIteration(
                        iteration_index=iteration, plan_snapshot=plan,
                        decision="rejected", generated_at=gen_at,
                        model_name=self.model_name,
                    ))
                    raise PlanRejectedError(iterations=iteration)
                feedback = str(response)
                provenance.record_plan_iteration(PlanIteration(
                    iteration_index=iteration, plan_snapshot=plan,
                    user_feedback=feedback, decision="feedback",
                    generated_at=gen_at, model_name=self.model_name,
                ))
                up(f"Revising strategy with feedback: {feedback[:80]}...")
                strategy = await run_search_strategy(self.deps, user_feedback=feedback)
            raise MaxIterationsReachedError(max_plan_iterations, max_plan_iterations)
        # Auto-confirmed: record the single plan
        plan = _build_review_plan(strategy, proto.question, iteration=1)
        provenance.record_plan_iteration(PlanIteration(
            iteration_index=1, plan_snapshot=plan,
            decision="auto_confirmed",
            generated_at=datetime.now(_tz.utc).isoformat(),
            model_name=self.model_name,
        ))
        return strategy

    async def run(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,  # existing — unchanged
        data_items: Optional[list[str]] = None,                     # existing — unchanged
        auto_confirm: bool = False,           # new — False: show confirmation gate; True: skip it
        confirm_callback: Optional[Callable[[ReviewPlan], "bool | str"]] = None,  # new — None: CLI input or auto
        max_plan_iterations: int = 3,         # new — max re-generation attempts before MaxIterationsReachedError
        output_synthesis_style: str = "paragraph",  # new — controls PrismaReview results rendering style: "paragraph" | "question_answer" | "bullet_list" | "table"
        assemble_timeout: float = 3600.0,     # max seconds for the two-wave assembly gather; raises asyncio.TimeoutError on breach
        checkpoint: "dict | None" = None,
        on_checkpoint: "Optional[Callable]" = None,
    ) -> PRISMAReviewResult:
        if not self.deps.api_key:
            raise ValueError(
                "api_key is required — set OPENROUTER_API_KEY or pass api_key to PRISMAReviewPipeline"
            )
        proto = self.protocol
        flow = PRISMAFlowCounts()
        all_articles: dict[str, Article] = {}
        seen_pmids: set[str] = set()

        # Provenance collector for this run — captures plan iterations,
        # agent invocations, search iterations, and run configuration.
        provenance = ProvenanceCollector()
        provenance.run_configuration = build_run_configuration(
            protocol=proto,
            review_id=proto.review_id or "",
            model_name=self.model_name,
            pipeline_kwargs={
                "auto_confirm": auto_confirm,
                "max_plan_iterations": max_plan_iterations,
                "output_synthesis_style": output_synthesis_style,
                "assemble_timeout": assemble_timeout,
                "data_items": data_items,
                "max_per_query": self.max_per_query,
                "related_depth": self.related_depth,
                "biorxiv_days": self.biorxiv_days,
            },
        )
        self.deps.provenance = provenance

        _ckpt: dict = checkpoint or {}
        _ckpt_step: int = _ckpt.get("last_completed_step", 0)

        async def _save_ckpt(step: int, extra: dict) -> None:
            nonlocal _ckpt, _ckpt_step
            if on_checkpoint:
                merged = {**_ckpt, **extra, "last_completed_step": step}
                import inspect
                result = on_checkpoint(merged)
                if inspect.isawaitable(result):
                    await result
                _ckpt = merged
                _ckpt_step = step

        async def _save_ckpt_partial(extra: dict) -> None:
            """Save intermediate checkpoint without advancing _ckpt_step or last_completed_step."""
            nonlocal _ckpt
            if on_checkpoint:
                import inspect
                merged = {**_ckpt, **extra}
                result = on_checkpoint(merged)
                if inspect.isawaitable(result):
                    await result
                _ckpt = merged

        def up(msg: str):
            self.log(msg)
            if progress_callback:
                progress_callback(msg)

        # ── 0. PostgreSQL review-result cache check ──
        pg_store: Optional[CacheStore] = None
        art_store: Optional[ArticleStore] = None
        criteria_dict = proto.model_dump()

        # Track which pipeline stages are already complete in DB (feature 010 resume)
        _completed_stages: set[str] = set()

        if _CACHE_AVAILABLE and proto.pg_dsn:
            try:
                pg_store = CacheStore(dsn=proto.pg_dsn)
                await pg_store.connect()
                art_store = ArticleStore(dsn=proto.pg_dsn)
                await art_store.connect()

                # T036: force_refresh clears all checkpoints before querying
                if proto.force_refresh and proto.review_id:
                    up("Force-refresh: clearing pipeline checkpoints...")
                    try:
                        await pg_store.clear_checkpoints(proto.review_id)
                    except Exception as exc:
                        logger.warning("Could not clear checkpoints: %s", exc)

                # T033: load completed stages for this review
                if proto.review_id:
                    try:
                        _completed_stages = await pg_store.load_completed_stages(proto.review_id)
                        if _completed_stages:
                            up(f"Resume: {len(_completed_stages)} stage(s) already complete — "
                               f"{', '.join(sorted(_completed_stages))}")
                    except Exception as exc:
                        logger.warning("Could not load completed stages: %s", exc)

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
        _effective_auto = auto_confirm
        if not auto_confirm and confirm_callback is None and not sys.stdin.isatty():
            logger.warning(
                "Non-interactive environment detected; defaulting to auto_confirm=True"
            )
            _effective_auto = True

        strategy = await self._resolve_plan_with_iterations(
            strategy, provenance, confirm_callback,
            max_plan_iterations, _effective_auto, up,
        )

        pubmed_queries = strategy.pubmed_queries or [proto.question]
        biorxiv_queries = strategy.biorxiv_queries or []
        all_search_queries = pubmed_queries + biorxiv_queries

        # Search-iteration recorder (one record per query / hop).
        import time as _time
        from datetime import timezone as _tz
        _search_idx = [0]

        def _record_search(*, kind: str, db: str, query: str,
                           seed_pmids: list, new_pmids: list, dur_ms: float) -> None:
            _search_idx[0] += 1
            provenance.record_search_iteration(SearchIteration(
                iteration_index=_search_idx[0],
                iteration_kind=kind,
                database=db,
                query=query,
                seed_pmids=list(seed_pmids),
                new_pmids=list(new_pmids),
                cumulative_count=len(all_articles),
                duration_ms=dur_ms,
                started_at=datetime.now(_tz.utc).isoformat(),
            ))

        # ── 2. PubMed search ──
        for i, q in enumerate(pubmed_queries):
            up(f"PubMed search {i+1}/{len(pubmed_queries)}: {q[:60]}...")
            _t0 = _time.monotonic()
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
            _record_search(
                kind="initial_query", db="pubmed", query=q,
                seed_pmids=[], new_pmids=new,
                dur_ms=(_time.monotonic() - _t0) * 1000.0,
            )
        _apply_per_db_tally(flow, all_articles)

        # ── 3. bioRxiv / medRxiv search ──
        if "bioRxiv" in proto.databases and biorxiv_queries:
            for bq in biorxiv_queries[:3]:
                up(f"bioRxiv search: {bq[:60]}...")
                _t0 = _time.monotonic()
                bx_arts = self.biorxiv.search(bq, 10, self.biorxiv_days)
                _new = [a.pmid for a in bx_arts if a.pmid not in all_articles]
                for a in bx_arts:
                    if a.pmid not in all_articles:
                        all_articles[a.pmid] = a
                up(f"  Found {len(bx_arts)} preprints")
                _record_search(
                    kind="initial_query", db="biorxiv", query=bq,
                    seed_pmids=[], new_pmids=_new,
                    dur_ms=(_time.monotonic() - _t0) * 1000.0,
                )
        _apply_per_db_tally(flow, all_articles)

        if "medRxiv" in proto.databases and biorxiv_queries:
            for bq in biorxiv_queries[:3]:
                up(f"medRxiv search: {bq[:60]}...")
                _t0 = _time.monotonic()
                mx_arts = self.medrxiv.search(bq, 10, self.biorxiv_days)
                _new = [a.pmid for a in mx_arts if a.pmid not in all_articles]
                for a in mx_arts:
                    if a.pmid not in all_articles:
                        all_articles[a.pmid] = a
                up(f"  Found {len(mx_arts)} preprints")
                _record_search(
                    kind="initial_query", db="medrxiv", query=bq,
                    seed_pmids=[], new_pmids=_new,
                    dur_ms=(_time.monotonic() - _t0) * 1000.0,
                )

        # ── 4. Related articles ──
        pm_pmids = [
            a.pmid for a in all_articles.values()
            if not a.pmid.startswith("biorxiv_")
        ]
        if pm_pmids:
            seeds = pm_pmids[:8]
            for d in range(1, self.related_depth + 1):
                up(f"Finding related articles (depth {d})...")
                _t0 = _time.monotonic()
                rel_pmids = self.pubmed.find_related(seeds, max_results=15)
                new_rel = [p for p in rel_pmids if p not in all_articles]
                if new_rel:
                    rel_arts = self.pubmed.fetch_articles(new_rel)
                    for a in rel_arts:
                        a.source = f"related_{d}"
                        all_articles[a.pmid] = a
                    _record_search(
                        kind="related_articles", db="pubmed",
                        query=f"related-depth-{d}", seed_pmids=seeds,
                        new_pmids=new_rel,
                        dur_ms=(_time.monotonic() - _t0) * 1000.0,
                    )
                    seeds = [a.pmid for a in rel_arts[:5]]
                    up(f"  Depth {d}: {len(rel_arts)} articles")
                else:
                    break

        # ── 5. Multi-hop citation navigation ──
        if proto.max_hops > 0 and pm_pmids:
            hop_seeds = pm_pmids[:5]
            for hop in range(1, proto.max_hops + 1):
                up(f"Citation hop {hop}...")
                _t0 = _time.monotonic()
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
                    _record_search(
                        kind="citation_hop", db="pubmed",
                        query=f"citation-hop-{hop}",
                        seed_pmids=hop_seeds,
                        new_pmids=[a.pmid for a in hop_arts],
                        dur_ms=(_time.monotonic() - _t0) * 1000.0,
                    )
                    hop_seeds = [a.pmid for a in hop_arts[:5]]
                    up(f"  Hop {hop}: {len(hop_arts)} articles")
                else:
                    break
        _apply_per_db_tally(flow, all_articles)
        flow.total_identified = len(all_articles)
        up(f"Total identified: {flow.total_identified}")

        # ── 6. Deduplication ──
        if _ckpt_step >= 6:
            deduped = [Article(**a) for a in _ckpt.get("deduped_articles", [])]
            all_search_queries = _ckpt.get("search_queries", all_search_queries)
            flow = PRISMAFlowCounts(**_ckpt["flow"]) if "flow" in _ckpt else flow
            up("Loaded dedup from checkpoint (step 6)")
        else:
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
            await _save_ckpt(6, {
                "deduped_articles": [a.model_dump() for a in deduped],
                "search_queries": all_search_queries,
                "flow": flow.model_dump(),
            })

        return _AcquisitionResult(
            strategy=strategy,
            all_search_queries=all_search_queries,
            deduped=deduped,
            all_articles=all_articles,
            flow=flow,
        )

    async def run(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,  # existing — unchanged
        data_items: Optional[list[str]] = None,                     # existing — unchanged
        auto_confirm: bool = False,           # new — False: show confirmation gate; True: skip it
        confirm_callback: Optional[Callable[[ReviewPlan], "bool | str"]] = None,  # new — None: CLI input or auto
        max_plan_iterations: int = 3,         # new — max re-generation attempts before MaxIterationsReachedError
        output_synthesis_style: str = "paragraph",  # new — controls PrismaReview results rendering style: "paragraph" | "question_answer" | "bullet_list" | "table"
    ) -> PRISMAReviewResult:
        if not self.deps.api_key:
            raise ValueError(
                "api_key is required — set OPENROUTER_API_KEY or pass api_key to PRISMAReviewPipeline"
            )
        proto = self.protocol
        all_screening: list[ScreeningLogEntry] = []

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
                        up("Cache miss — running full pipeline.")
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

        # ── Steps 1–6: Shared acquisition (search strategy + HTTP + dedup) ──
        acq = await self._fetch_articles(
            proto, up,
            auto_confirm=auto_confirm,
            confirm_callback=confirm_callback,
            max_plan_iterations=max_plan_iterations,
        )
        strategy = acq.strategy
        all_search_queries = acq.all_search_queries
        deduped = acq.deduped
        all_articles = acq.all_articles
        flow = acq.flow

        if not deduped:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto, flow=flow,
                synthesis_text="No articles found matching the search criteria.",
                timestamp=datetime.now().isoformat(),
            )

        # ── 6b. Relevance reranking + cap (optional) ──
        if proto.max_articles and len(deduped) > proto.max_articles:
            before = len(deduped)
            deduped = _rerank_articles(deduped, proto.question, proto.max_articles)
            up(f"Reranked and capped: {before} → {len(deduped)} articles (--max-articles {proto.max_articles})")

        # ── 7. Title/abstract screening (LLM agent, batches of 15) ──
        ta_included: list[Article] = []
        if _ckpt_step >= 7:
            ta_included = [Article(**a) for a in _ckpt.get("ta_included", [])]
            all_screening = [ScreeningLogEntry(**e) for e in _ckpt.get("screening_log", [])]
            ta_excluded: list[Article] = []  # not persisted in checkpoint
            up("Loaded T/A screening from checkpoint (step 7)")
        else:
            up(f"Screening {len(deduped)} articles (title/abstract, concurrency={proto.article_concurrency})...")
            flow.screened_title_abstract = len(deduped)
            ta_included, ta_excluded, _ta_log = await _parallel_ta_screening(
                deduped, self.deps, proto.article_concurrency, up,
            )
            all_screening.extend(_ta_log)
            flow.excluded_title_abstract = len(ta_excluded)
            up(f"Screening: {len(ta_included)} included, {len(ta_excluded)} excluded")
            # Final step-7 checkpoint: ta_screening_batch_offset intentionally absent
            await _save_ckpt(7, {
                "ta_included": [a.model_dump() for a in ta_included],
                "screening_log": [e.model_dump() for e in all_screening],
            })

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

        # 8a. Multi-source full-text resolver for articles still missing full text.
        # Tries Europe PMC OA full text → preprint PDFs (bioRxiv/medRxiv) →
        # DOI chain (Unpaywall → OpenAlex → Semantic Scholar) → marker-pdf parse.
        unresolved = [a for a in ta_included if not a.full_text]
        if unresolved:
            up(f"Resolving full text for {len(unresolved)} remaining articles via OA chain...")
            resolved = 0
            for a in unresolved:
                try:
                    if await asyncio.to_thread(self.full_text_resolver.resolve, a):
                        resolved += 1
                except Exception as exc:
                    logger.info("Full-text resolver error on %s: %s", a.pmid, exc)
            up(f"  Resolver retrieved {resolved}/{len(unresolved)} additional full texts")

        # 8a-bis. Compute SHA-256 content hashes for reproducibility.
        for a in ta_included:
            if a.full_text and not a.content_sha256:
                a.content_sha256 = content_sha256(a.full_text)

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
        ft_included: list[Article] = []
        if _ckpt_step >= 9:
            ft_included = [Article(**a) for a in _ckpt.get("ft_included", [])]
            all_screening = [ScreeningLogEntry(**e) for e in _ckpt.get("screening_log", [])]
            flow = PRISMAFlowCounts(**_ckpt["flow"]) if "flow" in _ckpt else flow
            up("Loaded FT screening from checkpoint (step 9)")
        else:
            ft_articles = [a for a in ta_included if a.full_text]
            no_ft = [a for a in ta_included if not a.full_text]
            ft_included = list(no_ft)
            ft_excluded: list[Article] = []

            if ft_articles:
                up(f"Full-text eligibility screening ({len(ft_articles)} articles, concurrency={proto.article_concurrency})...")
                _ft_inc, _ft_exc, _ft_log = await _parallel_ft_screening(
                    ft_articles, self.deps, proto.article_concurrency, up,
                )
                ft_included.extend(_ft_inc)
                ft_excluded.extend(_ft_exc)
                all_screening.extend(_ft_log)

            flow.assessed_eligibility = len(ta_included)
            flow.excluded_eligibility = len(ft_excluded)
            flow.included_synthesis = len(ft_included)

            # Tally exclusion reasons (ta_excluded may be empty if loaded from step-7 ckpt)
            reason_counts: dict[str, int] = defaultdict(int)
            for a in ta_excluded + ft_excluded:
                r = a.exclusion_reason[:60] if a.exclusion_reason else "Unspecified"
                reason_counts[r] += 1
            flow.excluded_reasons = dict(
                sorted(reason_counts.items(), key=lambda x: -x[1])[:8]
            )

            up(f"Final included: {flow.included_synthesis} articles")
            await _save_ckpt(9, {
                "ft_included": [a.model_dump() for a in ft_included],
                "screening_log": [e.model_dump() for e in all_screening],
                "flow": flow.model_dump(),
            })

        if not ft_included:
            return PRISMAReviewResult(
                research_question=proto.question,
                protocol=proto, search_queries=all_search_queries,
                flow=flow, screening_log=all_screening,
                synthesis_text="All articles excluded during eligibility assessment.",
                timestamp=datetime.now().isoformat(),
            )

        # ── 10. Evidence span extraction (no LLM) ──
        evidence: list[EvidenceSpan] = []
        if _ckpt_step >= 10:
            evidence = [EvidenceSpan(**e) for e in _ckpt.get("evidence", [])]
            up("Loaded evidence from checkpoint (step 10)")
        else:
            _n_ev_batches = max(1, (len(ft_included) + 4) // 5)  # 5 articles per batch
            up(f"Extracting evidence spans — {len(ft_included)} articles in ~{_n_ev_batches} batches (concurrency={proto.article_concurrency})...")
            evidence = await extract_evidence(ft_included, self.deps, concurrency=proto.article_concurrency)
            up(f"Extracted {len(evidence)} evidence spans from {len(ft_included)} articles")
            await _save_ckpt(10, {"evidence": [e.model_dump() for e in evidence]})

        # ── Setup for the per-article DAG and downstream synthesis ──
        flow_text = (
            f"Identified: {flow.total_identified} | "
            f"After dedup: {flow.after_dedup} | "
            f"Screened: {flow.screened_title_abstract} | "
            f"Excluded (screening): {flow.excluded_title_abstract} | "
            f"Full-text assessed: {flow.assessed_eligibility} | "
            f"Excluded (eligibility): {flow.excluded_eligibility} | "
            f"Included: {flow.included_synthesis}"
        )
        charting_questions = list(proto.charting_questions) if proto.charting_questions else None
        appraisal_domains = list(proto.appraisal_domains) if proto.appraisal_domains else None
        charting_template = proto.charting_template or default_charting_template()
        appraisal_config = proto.critical_appraisal_config or default_appraisal_config()
        _resolved_cfg = _resolve_section_config(self.protocol)

        # ── 11–15. Per-article DAG: RoB + Extract + (Chart → Appraise → Narrate)
        # Every article runs as one independent task. Within the task three
        # legs fire concurrently:
        #
        #   (1) Risk of bias                          — independent
        #   (2) Per-study data extraction (if data_items)  — independent
        #   (3) Charting → Appraisal → Narrative      — sequential chain
        #
        # All article tasks themselves run concurrently, gated by
        # ``proto.article_concurrency``. With concurrency=N this fans out to
        # at most N×3 in-flight LLM calls (since each article holds its slot
        # for the duration of its slowest leg, with up to 3 calls in flight
        # for that article at a time).
        #
        # Eliminates four corpus-wide barriers the old layout had
        # (extract→RoB→chart→appraise→narrate), so a slow article never
        # blocks faster ones.
        #
        # Each leg with DB checkpointing still uses ``_load_or_run_batch`` so
        # per-article-per-stage resume works exactly as before. Local-fixture
        # resume at ``_ckpt_step >= 15`` short-circuits the whole block.
        data_charting_rubrics: list[DataChartingRubric] = []
        critical_appraisals: list[CriticalAppraisalRubric] = []
        critical_appraisal_results: list[CriticalAppraisalResult] = []
        narrative_rows: list[PRISMANarrativeRow] = []

        if _ckpt_step >= 15:
            data_charting_rubrics = [DataChartingRubric(**r) for r in _ckpt.get("data_charting_rubrics", [])]
            critical_appraisals = [CriticalAppraisalRubric(**r) for r in _ckpt.get("critical_appraisals", [])]
            narrative_rows = [PRISMANarrativeRow(**r) for r in _ckpt.get("narrative_rows", [])]
            _ckpt_ft = _ckpt.get("ft_included")
            if _ckpt_ft:
                ft_included = [Article(**a) for a in _ckpt_ft]
            up("Loaded RoB/extract/charting/appraisal/narrative from checkpoint (step 15)")
        elif (
            STAGE_NARRATIVE in _completed_stages
            and STAGE_APPRAISAL in _completed_stages
            and STAGE_CHARTING in _completed_stages
        ):
            if pg_store and proto.review_id:
                # Hydrate RoB onto each article from DB if STAGE_ROB is present.
                if STAGE_ROB in _completed_stages:
                    rob_ckpts = await pg_store.load_checkpoints(proto.review_id, STAGE_ROB)
                    rob_map = {
                        c.batch_index: c.result_json.get("rob")
                        for c in rob_ckpts if c.status == "complete" and "rob" in c.result_json
                    }
                    for i, art in enumerate(ft_included):
                        if i in rob_map and rob_map[i]:
                            art.risk_of_bias = RiskOfBiasResult(**rob_map[i])
                chart_ckpts = await pg_store.load_checkpoints(proto.review_id, STAGE_CHARTING)
                data_charting_rubrics = [
                    DataChartingRubric(**c.result_json["rubric"])
                    for c in chart_ckpts if c.status == "complete" and "rubric" in c.result_json
                ]
                appr_ckpts = await pg_store.load_checkpoints(proto.review_id, STAGE_APPRAISAL)
                critical_appraisals = [
                    CriticalAppraisalRubric(**c.result_json["appraisal"])
                    for c in appr_ckpts if c.status == "complete" and "appraisal" in c.result_json
                ]
                narr_ckpts = await pg_store.load_checkpoints(proto.review_id, STAGE_NARRATIVE)
                narrative_rows = [
                    PRISMANarrativeRow(**c.result_json["row"])
                    for c in narr_ckpts if c.status == "complete" and "row" in c.result_json
                ]
                up(
                    f"Resume: loaded {len(data_charting_rubrics)} charting / "
                    f"{len(critical_appraisals)} appraisal / {len(narrative_rows)} narrative from DB"
                )
            logger.info(
                "stage_resumed stage=%s+%s+%s",
                STAGE_CHARTING, STAGE_APPRAISAL, STAGE_NARRATIVE,
            )
        else:
            n = len(ft_included)
            extract_note = " + extract" if data_items else ""
            up(
                f"Per-article DAG: RoB{extract_note} + chart→appraise→narrate "
                f"(concurrency={proto.article_concurrency})..."
            )
            _dag_sem = asyncio.Semaphore(proto.article_concurrency)
            _chart_r: list[DataChartingRubric | None] = [None] * n
            _appraisal_r: list[CriticalAppraisalRubric | None] = [None] * n
            _appraisal_rs: list[CriticalAppraisalResult | None] = [None] * n
            _narr_r: list[PRISMANarrativeRow | None] = [None] * n
            _done = [0]

            async def _process_article(idx: int, article: Article) -> None:
                async with _dag_sem:
                    # ── Leg A: Risk of bias (independent, DB-checkpointed) ──
                    async def _rob_leg() -> None:
                        try:
                            async def _do_rob(_a=article) -> dict:
                                rob = await run_risk_of_bias(_a, self.deps)
                                return {"rob": rob.model_dump()}

                            payload = await _load_or_run_batch(
                                pg_store, proto.review_id or "", STAGE_ROB, idx,
                                _do_rob, proto.max_batch_retries,
                            )
                            if "rob" in payload:
                                article.risk_of_bias = RiskOfBiasResult(**payload["rob"])
                        except Exception as exc:
                            logger.warning("RoB failed for %s: %s", article.pmid, exc)

                    # ── Leg B: Per-study data extraction (independent, no DB ckpt) ──
                    async def _extract_leg() -> None:
                        if not data_items:
                            return
                        try:
                            article.extracted_data = await run_data_extraction(
                                article, data_items, self.deps,
                            )
                        except Exception as exc:
                            logger.warning("Data extraction failed for %s: %s", article.pmid, exc)

                    # ── Leg C: Charting → Appraisal → Narrative (chained) ──
                    async def _chain_leg() -> None:
                        # Charting
                        try:
                            async def _do_charting(_art=article) -> dict:
                                rubric_local = await run_data_charting(
                                    _art, self.deps, charting_questions,
                                    resolved_section_config=_resolved_cfg,
                                    charting_template=charting_template,
                                )
                                return {"rubric": rubric_local.model_dump()}

                            payload = await _load_or_run_batch(
                                pg_store, proto.review_id or "", STAGE_CHARTING, idx,
                                _do_charting, proto.max_batch_retries,
                            )
                            rubric = DataChartingRubric(**payload["rubric"])
                            _chart_r[idx] = rubric
                        except Exception as exc:
                            logger.warning("Charting failed for %s: %s", article.pmid, exc)
                            return

                        # Appraisal (consumes rubric)
                        try:
                            async def _do_appraisal(_art=article, _rub=rubric) -> dict:
                                ap_rubric_local, ap_result_local = await run_critical_appraisal(
                                    _art, _rub, self.deps, appraisal_domains,
                                    appraisal_config=appraisal_config,
                                )
                                return {
                                    "appraisal": ap_rubric_local.model_dump(),
                                    "result": ap_result_local.model_dump(),
                                }

                            payload = await _load_or_run_batch(
                                pg_store, proto.review_id or "", STAGE_APPRAISAL, idx,
                                _do_appraisal, proto.max_batch_retries,
                            )
                            ap_rubric = CriticalAppraisalRubric(**payload["appraisal"])
                            ap_res = (
                                CriticalAppraisalResult(**payload["result"])
                                if "result" in payload else None
                            )
                            _appraisal_r[idx] = ap_rubric
                            _appraisal_rs[idx] = ap_res
                        except Exception as exc:
                            logger.warning("Appraisal failed for %s: %s", article.pmid, exc)
                            return

                        # Narrative (consumes rubric + appraisal)
                        try:
                            async def _do_narrative(_rub=rubric, _ap=ap_rubric) -> dict:
                                row = await run_narrative_row(_rub, _ap, self.deps)
                                return {"row": row.model_dump()}

                            payload = await _load_or_run_batch(
                                pg_store, proto.review_id or "", STAGE_NARRATIVE, idx,
                                _do_narrative, proto.max_batch_retries,
                            )
                            _narr_r[idx] = PRISMANarrativeRow(**payload["row"])
                        except Exception as exc:
                            logger.warning("Narrative row failed for %s: %s", article.pmid, exc)

                    # Three legs run as siblings; chain leg gates on its own
                    # internal sequential dependencies, RoB + extract are
                    # truly independent.
                    await asyncio.gather(_rob_leg(), _extract_leg(), _chain_leg())

                    _done[0] += 1
                    up(
                        f"  ✓ {article.pmid} per-article work complete "
                        f"[{_done[0]}/{n} done, {n - _done[0]} remaining]"
                    )

            await asyncio.gather(*[_process_article(i, art) for i, art in enumerate(ft_included)])

            # Reconcile: keep only articles where every leg succeeded so the
            # downstream lists stay aligned by index. Articles that failed any
            # leg are dropped from `ft_included` (mirrors the old behaviour
            # where charting failures filtered the list).
            keep = [
                i for i in range(n)
                if _chart_r[i] is not None and _appraisal_r[i] is not None and _narr_r[i] is not None
            ]
            ft_included = [ft_included[i] for i in keep]
            data_charting_rubrics = [_chart_r[i] for i in keep]  # type: ignore[misc]
            critical_appraisals = [_appraisal_r[i] for i in keep]  # type: ignore[misc]
            critical_appraisal_results = [
                _appraisal_rs[i] for i in keep if _appraisal_rs[i] is not None
            ]  # type: ignore[misc]
            narrative_rows = [_narr_r[i] for i in keep]  # type: ignore[misc]

            await _save_ckpt(15, {
                "data_charting_rubrics": [r.model_dump() for r in data_charting_rubrics],
                "critical_appraisals": [r.model_dump() for r in critical_appraisals],
                "narrative_rows": [r.model_dump() for r in narrative_rows],
                "ft_included": [a.model_dump() for a in ft_included],
            })

        # ── 16. Grounded synthesis (LLM agent, chunked for large reviews) ──
        synthesis = ""
        if _ckpt_step >= 16:
            synthesis = _ckpt.get("synthesis_text", "")
            up("Loaded synthesis from checkpoint (step 16)")
        elif STAGE_SYNTHESIS in _completed_stages and STAGE_SYNTHESIS_MERGE in _completed_stages:
            if pg_store and proto.review_id:
                merge_ckpts = await pg_store.load_checkpoints(proto.review_id, STAGE_SYNTHESIS_MERGE)
                if merge_ckpts and merge_ckpts[0].status == "complete":
                    synthesis = merge_ckpts[0].result_json.get("synthesis_text", "")
                    up("Resume: loaded merged synthesis from DB")
            logger.info("stage_resumed stage=%s+%s", STAGE_SYNTHESIS, STAGE_SYNTHESIS_MERGE)
        else:
            batch_size = proto.synthesis_batch_size
            batches = [ft_included[i:i + batch_size] for i in range(0, len(ft_included), batch_size)]
            up(f"Synthesizing {len(ft_included)} articles in {len(batches)} chunk(s) "
               f"(batch_size={batch_size})...")
            partial_syntheses: list[str] = []
            for idx, batch in enumerate(batches):
                async def _do_synthesis(_b=batch) -> dict:
                    text = await run_synthesis(_b, evidence, flow_text, self.deps)
                    return {"synthesis_text": text}

                payload = await _load_or_run_batch(
                    pg_store, proto.review_id or "", STAGE_SYNTHESIS, idx,
                    _do_synthesis, proto.max_batch_retries,
                )
                partial_syntheses.append(payload.get("synthesis_text", ""))
                up(f"  Synthesis chunk {idx + 1}/{len(batches)} done")

            # Merge partial syntheses (skip merge agent when only one chunk)
            if len(partial_syntheses) == 1:
                synthesis = partial_syntheses[0]
            else:
                logger.info("synthesis_merge_start partial_count=%d", len(partial_syntheses))
                up(f"Merging {len(partial_syntheses)} synthesis chunks...")

                async def _do_merge() -> dict:
                    merged = await run_synthesis_merge_agent(partial_syntheses, self.deps)
                    return {"synthesis_text": merged}

                merge_payload = await _load_or_run_batch(
                    pg_store, proto.review_id or "", STAGE_SYNTHESIS_MERGE, 0,
                    _do_merge, proto.max_batch_retries,
                )
                synthesis = merge_payload.get("synthesis_text", "\n\n".join(partial_syntheses))
                logger.info("synthesis_merge_complete")

            await _save_ckpt(16, {"synthesis_text": synthesis})

        # ── 17. Grounding validation ──
        grounding_validation = None
        if _ckpt_step >= 17:
            gv_data = _ckpt.get("grounding_validation")
            grounding_validation = GroundingValidationResult(**gv_data) if gv_data else None
            up("Loaded grounding validation from checkpoint (step 17)")
        else:
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
            await _save_ckpt(17, {
                "grounding_validation": grounding_validation.model_dump() if grounding_validation else None,
            })

        # ── 18. Assembly (bias, GRADE, limitations, introduction, conclusions, abstract) ──
        bias_text = ""
        limitations_text = ""
        grade_assessments: dict[str, GRADEAssessment] = {}
        introduction_text = ""
        conclusions_text = ""
        structured_abstract = ""
        if _ckpt_step >= 18:
            bias_text = _ckpt.get("bias_text", "")
            limitations_text = _ckpt.get("limitations", "")
            grade_assessments = {
                k: GRADEAssessment(**v) for k, v in _ckpt.get("grade_assessments", {}).items()
            }
            introduction_text = _ckpt.get("introduction_text", "")
            conclusions_text = _ckpt.get("conclusions_text", "")
            structured_abstract = _ckpt.get("structured_abstract", "")
            up("Loaded assembly from checkpoint (step 18)")
        else:
            up("Assessing overall bias and GRADE...")
            bias_task = run_bias_summary(ft_included, self.deps)
            outcome_text = proto.pico_outcome or "Primary outcome"
            outcomes = [o.strip() for o in outcome_text.split(",") if o.strip()]
            # Run GRADE for every PICO outcome — already gathered concurrently
            # below, so all outcomes complete in roughly one round-trip.
            grade_tasks = {
                outcome: run_grade(outcome, ft_included, self.deps)
                for outcome in outcomes
            }
            limitations_task = run_limitations(flow_text, ft_included, self.deps)

            bias_result, limitations_result, *grade_results = await asyncio.gather(
                bias_task,
                limitations_task,
                *grade_tasks.values(),
                return_exceptions=True,
            )
            bias_text = bias_result if isinstance(bias_result, str) else ""
            limitations_text = limitations_result if isinstance(limitations_result, str) else ""
            for outcome, result in zip(grade_tasks.keys(), grade_results):
                if isinstance(result, GRADEAssessment):
                    grade_assessments[outcome] = result

            grade_summary = "; ".join(
                f"{k}: {v.overall_certainty.value}" for k, v in grade_assessments.items()
            )
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

            await _save_ckpt(18, {
                "bias_text": bias_text,
                "limitations": limitations_text,
                "grade_assessments": {k: v.model_dump() for k, v in grade_assessments.items()},
                "introduction_text": introduction_text,
                "conclusions_text": conclusions_text,
                "structured_abstract": structured_abstract,
            })

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

        # Stamp provenance onto the final result.
        provenance.stamp(final_result)

        # ── Persist result + provenance trail to PostgreSQL ──
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
                # Provenance telemetry — only when we have a stable review_id.
                if proto.review_id:
                    try:
                        ok = await pg_store.store_telemetry(
                            review_id=proto.review_id,
                            run_configuration=(
                                provenance.run_configuration.model_dump(mode="json")
                                if provenance.run_configuration else None
                            ),
                            plan_iterations=[
                                p.model_dump(mode="json")
                                for p in provenance.plan_iterations
                            ],
                            agent_invocations=[
                                i.model_dump(mode="json")
                                for i in provenance.agent_invocations
                            ],
                            search_iterations=[
                                s.model_dump(mode="json")
                                for s in provenance.search_iterations
                            ],
                        )
                        if ok:
                            up(f"Provenance trail stored "
                               f"({len(provenance.agent_invocations)} invocations, "
                               f"{len(provenance.plan_iterations)} plan iter, "
                               f"{len(provenance.search_iterations)} search iter).")
                    except Exception as exc:
                        logger.warning("Failed to store telemetry (%s).", exc)
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

        # Provenance collector for the shared article-acquisition phase
        # (compare-mode reuses this across sub-pipelines).
        provenance = self.deps.provenance or ProvenanceCollector()
        if self.deps.provenance is None:
            self.deps.provenance = provenance

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

        strategy = await self._resolve_plan_with_iterations(
            strategy, provenance, confirm_callback,
            max_plan_iterations, _effective_auto, up,
        )

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
        _apply_per_db_tally(flow, all_articles)

        # 3. bioRxiv search
        if "bioRxiv" in proto.databases and biorxiv_queries:
            for bq in biorxiv_queries[:3]:
                up(f"bioRxiv search: {bq[:60]}...")
                bx_arts = self.biorxiv.search(bq, 10, self.biorxiv_days)
                for a in bx_arts:
                    if a.pmid not in all_articles:
                        all_articles[a.pmid] = a
                up(f"  Found {len(bx_arts)} preprints")
        _apply_per_db_tally(flow, all_articles)

        # 4. Related articles
        pm_pmids = [a.pmid for a in all_articles.values() if not a.pmid.startswith("biorxiv_")]
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
                    seeds = [a.pmid for a in rel_arts[:5]]
                    up(f"  Depth {d}: {len(rel_arts)} articles")
                else:
                    break
        # 5. Citation hops
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
                    hop_seeds = [a.pmid for a in hop_arts[:5]]
                    up(f"  Hop {hop}: {len(hop_arts)} articles")
                else:
                    break
        _apply_per_db_tally(flow, all_articles)
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

        if self.protocol.max_articles and len(deduped) > self.protocol.max_articles:
            before = len(deduped)
            deduped = _rerank_articles(deduped, self.protocol.question, self.protocol.max_articles)
            flow.after_dedup = len(deduped)
            up(f"Reranked and capped: {before} → {len(deduped)} articles (--max-articles {self.protocol.max_articles})")

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

        # Per-sub-pipeline provenance collector for compare-mode (each
        # model gets its own AgentInvocation trail).
        if self.deps.provenance is None:
            self.deps.provenance = ProvenanceCollector()
            self.deps.provenance.run_configuration = build_run_configuration(
                protocol=proto,
                review_id=proto.review_id or "",
                model_name=self.model_name,
                pipeline_kwargs={
                    "compare_mode": True,
                    "output_synthesis_style": output_synthesis_style,
                    "assemble_timeout": assemble_timeout,
                    "data_items": data_items,
                },
            )

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
        up(f"Screening {len(deduped)} articles (title/abstract, concurrency={proto.article_concurrency})...")
        flow.screened_title_abstract = len(deduped)
        ta_included, ta_excluded, _ta_log_c = await _parallel_ta_screening(
            deduped, self.deps, proto.article_concurrency, up,
        )
        all_screening.extend(_ta_log_c)
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
            up(f"Full-text eligibility screening ({len(ft_articles)} articles, concurrency={proto.article_concurrency})...")
            _ft_inc_c, _ft_exc_c, _ft_log_c = await _parallel_ft_screening(
                ft_articles, self.deps, proto.article_concurrency, up,
            )
            ft_included.extend(_ft_inc_c)
            ft_excluded.extend(_ft_exc_c)
            all_screening.extend(_ft_log_c)

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
        _n_ev_batches_c = max(1, (len(ft_included) + 4) // 5)
        up(f"Extracting evidence spans — {len(ft_included)} articles in ~{_n_ev_batches_c} batches (concurrency={proto.article_concurrency})...")
        evidence = await extract_evidence(ft_included, self.deps, concurrency=proto.article_concurrency)
        up(f"Extracted {len(evidence)} evidence spans from {len(ft_included)} articles")

        # Per-article DAG legs 11/12 (RoB + extract) are now folded into the
        # block 15 DAG below — see the per-article DAG section a few blocks
        # down. The variables initialised there cover all per-article work
        # (RoB, extract, chart, appraise, narrate) in one fused fan-out.

        # 13+14. Synthesis + bias + GRADE + limitations — all independent, run concurrently
        flow_text = (
            f"Identified: {flow.total_identified} | After dedup: {flow.after_dedup} | "
            f"Screened: {flow.screened_title_abstract} | Excluded (TA): {flow.excluded_title_abstract} | "
            f"FT assessed: {flow.assessed_eligibility} | Excluded (FT): {flow.excluded_eligibility} | "
            f"Included: {flow.included_synthesis}"
        )
        outcome_text = proto.pico_outcome or "Primary outcome"
        outcomes = [o.strip() for o in outcome_text.split(",") if o.strip()]
        grade_tasks = {outcome: run_grade(outcome, ft_included, self.deps) for outcome in outcomes}
        up(f"Synthesizing {len(ft_included)} articles (+ bias, GRADE, limitations in parallel)...")
        (
            synthesis_raw,
            bias_result,
            limitations_result,
            *grade_results,
        ) = await asyncio.gather(
            run_synthesis(ft_included, evidence, flow_text, self.deps),
            run_bias_summary(ft_included, self.deps),
            run_limitations(flow_text, ft_included, self.deps),
            *grade_tasks.values(),
            return_exceptions=True,
        )
        synthesis = synthesis_raw if isinstance(synthesis_raw, str) else ""
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

        # 15. Charting + appraisal + narrative as a per-article DAG.
        # All articles run concurrently (semaphore-bounded); each article
        # streams chart → appraise → narrate without waiting on a corpus-wide
        # barrier between legs.
        _n = len(ft_included)
        _dag_sem_c = asyncio.Semaphore(proto.article_concurrency)
        _chart_r_c: list = [None] * _n
        _appr_rub_c: list = [None] * _n
        _appr_res_c: list = [None] * _n
        _narr_r_c: list = [None] * _n
        _done_c = [0]

        async def _process_article_c(idx: int, article: Article) -> None:
            async with _dag_sem_c:
                # ── Leg A: Risk of bias (independent) ──
                async def _rob_leg() -> None:
                    try:
                        article.risk_of_bias = await run_risk_of_bias(article, self.deps)
                    except Exception as exc:
                        logger.warning("RoB failed for %s: %s", article.pmid, exc)

                # ── Leg B: Per-study data extraction (independent) ──
                async def _extract_leg() -> None:
                    if not data_items:
                        return
                    try:
                        article.extracted_data = await run_data_extraction(
                            article, data_items, self.deps,
                        )
                    except Exception as exc:
                        logger.warning("Data extraction failed for %s: %s", article.pmid, exc)

                # ── Leg C: Charting → Appraisal → Narrative (chained) ──
                async def _chain_leg() -> None:
                    try:
                        rubric = await run_data_charting(
                            article, self.deps, charting_questions,
                            resolved_section_config=_resolved_cfg,
                            charting_template=charting_template,
                        )
                        _chart_r_c[idx] = rubric
                    except Exception as exc:
                        logger.warning("Charting failed for %s: %s", article.pmid, exc)
                        return

                    try:
                        ap_rubric, ap_result = await run_critical_appraisal(
                            article, rubric, self.deps, None,
                            appraisal_config=appraisal_config,
                        )
                        _appr_rub_c[idx] = ap_rubric
                        _appr_res_c[idx] = ap_result
                    except Exception as exc:
                        logger.warning("Appraisal failed for %s: %s", article.pmid, exc)
                        return

                    try:
                        row = await run_narrative_row(rubric, ap_rubric, self.deps)
                        _narr_r_c[idx] = row
                    except Exception as exc:
                        logger.warning("Narrative row failed for %s: %s", article.pmid, exc)

                await asyncio.gather(_rob_leg(), _extract_leg(), _chain_leg())

                _done_c[0] += 1
                up(
                    f"  ✓ {article.pmid} per-article work complete "
                    f"[{_done_c[0]}/{_n} done, {_n - _done_c[0]} remaining]"
                )

        extract_note = " + extract" if data_items else ""
        up(
            f"  Per-article DAG: RoB{extract_note} + chart→appraise→narrate "
            f"(concurrency={proto.article_concurrency})..."
        )
        await asyncio.gather(*[_process_article_c(i, art) for i, art in enumerate(ft_included)])

        # Reconcile lists by article index, dropping any with a failed leg.
        keep = [
            i for i in range(_n)
            if _chart_r_c[i] is not None and _appr_rub_c[i] is not None and _narr_r_c[i] is not None
        ]
        ft_included = [ft_included[i] for i in keep]
        data_charting_rubrics = [_chart_r_c[i] for i in keep]
        critical_appraisals = [_appr_rub_c[i] for i in keep]
        critical_appraisal_results = [
            _appr_res_c[i] for i in keep if _appr_res_c[i] is not None
        ]
        narrative_rows = [_narr_r_c[i] for i in keep]

        grade_summary = "; ".join(f"{k}: {v.overall_certainty.value}" for k, v in grade_assessments.items())
        flow_text_short = f"{flow.total_identified} identified, {flow.included_synthesis} included"
        corpus = {a.pmid: (a.abstract or "") + " " + (a.full_text or "") for a in ft_included}
        citation_map = {a.pmid: f"{a.authors} ({a.year})" for a in ft_included}
        up("Generating document sections and validating grounding…")
        grounding_validation = None
        introduction_text = conclusions_text = structured_abstract = ""
        try:
            (
                introduction_text_r,
                conclusions_text_r,
                structured_abstract_r,
                grounding_raw,
            ) = await asyncio.gather(
                run_introduction(self.deps),
                run_conclusions(synthesis, grade_summary, self.deps),
                run_abstract(flow_text_short, synthesis, self.deps),
                run_grounding_validation(synthesis, corpus, citation_map, self.deps),
                return_exceptions=True,
            )
            introduction_text = introduction_text_r if isinstance(introduction_text_r, str) else ""
            conclusions_text = conclusions_text_r if isinstance(conclusions_text_r, str) else ""
            structured_abstract = structured_abstract_r if isinstance(structured_abstract_r, str) else ""
            grounding_validation = grounding_raw if isinstance(grounding_raw, GroundingValidationResult) else None
            if isinstance(grounding_raw, BaseException):
                logger.warning("Grounding validation failed: %s", grounding_raw)
        except Exception as exc:
            logger.warning("Document section generation failed: %s", exc)

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

        # Stamp provenance onto compare-mode sub-pipeline result.
        if self.deps.provenance is not None:
            self.deps.provenance.stamp(final_result)

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
