"""
PRISMA Review Pipeline — async orchestrator.

Runs the full 15-step PRISMA 2020 pipeline using pydantic-ai agents
for LLM tasks and plain HTTP clients for data acquisition.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Callable, Optional

from models import (
    Article,
    ReviewProtocol,
    PRISMAFlowCounts,
    PRISMAReviewResult,
    ScreeningLogEntry,
    ScreeningStage,
    ScreeningDecisionType,
    EvidenceSpan,
    GRADEAssessment,
)
from clients import Cache, PubMedClient, BioRxivClient
from evidence import extract_evidence
from agents import (
    AgentDeps,
    run_search_strategy,
    run_screening,
    run_risk_of_bias,
    run_data_extraction,
    run_synthesis,
    run_grade,
    run_bias_summary,
    run_limitations,
)


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
        progress_callback: Optional[Callable[[str], None]] = None,
        data_items: Optional[list[str]] = None,
    ) -> PRISMAReviewResult:
        proto = self.protocol
        flow = PRISMAFlowCounts()
        all_screening: list[ScreeningLogEntry] = []
        all_articles: dict[str, Article] = {}
        seen_pmids: set[str] = set()

        def up(msg: str):
            self.log(msg)
            if progress_callback:
                progress_callback(msg)

        # ── 1. Search strategy (LLM agent) ──
        up("Generating search strategy...")
        strategy = await run_search_strategy(self.deps)
        pubmed_queries = strategy.pubmed_queries or [proto.question]
        biorxiv_queries = strategy.biorxiv_queries or []
        all_search_queries = pubmed_queries + biorxiv_queries
        up(f"Strategy: {len(pubmed_queries)} PubMed + {len(biorxiv_queries)} bioRxiv queries")
        up(f"Rationale: {strategy.rationale}")

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
                            art.inclusion_status = "included"
                            ta_included.append(art)
                        else:
                            art.inclusion_status = "excluded"
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
        pmc_articles = [a for a in ta_included if a.pmc_id]
        if pmc_articles:
            up(f"Fetching full text for {len(pmc_articles)} PMC articles...")
            texts = self.pubmed.fetch_full_text([a.pmc_id for a in pmc_articles])
            for a in pmc_articles:
                if a.pmc_id in texts:
                    a.full_text = texts[a.pmc_id]
            up(f"  Retrieved {len(texts)} full texts")
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
                                art.inclusion_status = "excluded"
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

        up("Review complete!")

        return PRISMAReviewResult(
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
        )
