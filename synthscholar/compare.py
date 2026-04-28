"""
Compare-mode pipeline: runs the same review protocol in parallel across N models (2–5).

Article acquisition (Steps 1–6) runs once and is shared.
All LLM-dependent steps (7–15) run independently per model via asyncio.gather.
"""

from __future__ import annotations

import asyncio
import logging
import warnings
from copy import deepcopy
from typing import Callable, Optional

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ_AVAILABLE = True
except ImportError:
    _RAPIDFUZZ_AVAILABLE = False

from .models import (
    CompareReviewResult,
    FieldAgreement,
    MergedReviewResult,
    ModelReviewRun,
    PRISMAReviewResult,
    ReviewPlan,
    SynthesisDivergence,
)
from .agents import AgentDeps, run_consensus_synthesis

logger = logging.getLogger(__name__)

_FALLBACK_CONSENSUS = "Insufficient successful model runs for consensus synthesis."


async def _run_model_pipeline(
    pipeline,
    deduped,
    all_search_queries: list[str],
    initial_flow,
    model_name: str,
    *,
    progress_callback: Optional[Callable[[str], None]] = None,
    data_items: Optional[list[str]] = None,
    output_synthesis_style: str = "paragraph",
    assemble_timeout: float = 3600.0,
) -> PRISMAReviewResult:
    """Run steps 7–15 for *model_name* using pre-fetched deduped articles."""
    from .pipeline import PRISMAReviewPipeline

    def up(msg: str) -> None:
        pipeline.log(f"[{model_name}] {msg}")
        if progress_callback:
            progress_callback(f"[{model_name}] {msg}")

    # Inherit the parent's resolved ncbi_api_key, email, and OA api_keys so
    # each sub-pipeline uses the same polite-pool credentials (whether the
    # parent picked them up from env vars, explicit constructor args, or the
    # SYNTHSCHOLAR_EMAIL / NCBI_EMAIL fallback). The parent's PubMedClient
    # and FullTextResolver already hold the resolved values.
    sub = PRISMAReviewPipeline(
        api_key=pipeline.deps.api_key,
        model_name=model_name,
        ncbi_api_key=pipeline.pubmed.api_key,
        email=pipeline.full_text_resolver.email,
        api_keys=pipeline.full_text_resolver.api_keys,
        protocol=pipeline.protocol,
        enable_cache=False,
        max_per_query=pipeline.max_per_query,
        related_depth=pipeline.related_depth,
        biorxiv_days=pipeline.biorxiv_days,
    )
    return await sub._run_from_deduped(
        deduped=deepcopy(deduped),
        all_search_queries=all_search_queries,
        initial_flow=deepcopy(initial_flow),
        progress_callback=up,
        data_items=data_items,
        output_synthesis_style=output_synthesis_style,
        assemble_timeout=assemble_timeout,
    )


def _compute_field_agreement(
    model_results: list[ModelReviewRun],
) -> dict[str, FieldAgreement]:
    """Compute per-field agreement across successful model runs.

    Key format: "{source_id}::{section_key}::{field_name}"
    Exact match (case-insensitive) for enumerated/yes_no_extended fields.
    token_set_ratio >= 80 for free_text/numeric fields (requires rapidfuzz).
    """
    succeeded = [r for r in model_results if r.succeeded and r.result]

    if len(succeeded) < 2:
        return {}

    # Build {key: {model_name: value}} mapping
    key_values: dict[str, dict[str, str]] = {}

    for run in succeeded:
        result = run.result
        if not result:
            continue
        # field_answers lives on StudyDataExtractionReport, not DataChartingRubric
        extraction_reports = (
            result.prisma_review.methods.data_extraction
            if result.prisma_review and result.prisma_review.methods
            else []
        )
        for report in extraction_reports:
            source_id = report.source_id
            for section_key, section_result in report.field_answers.items():
                if hasattr(section_result, "field_answers"):
                    for fa in section_result.field_answers:
                        if fa.value is None:
                            continue
                        key = f"{source_id}::{section_key}::{fa.field_name}"
                        key_values.setdefault(key, {})[run.model_name] = fa.value

    agreement: dict[str, FieldAgreement] = {}

    for key, values in key_values.items():
        # Only include fields present in ≥2 model results
        if len(values) < 2:
            continue

        field_name = key.split("::")[-1]
        vals = list(values.values())

        # Determine agreement
        canonical = vals[0].strip().lower()
        if _RAPIDFUZZ_AVAILABLE:
            agreed = all(
                _fuzz.token_set_ratio(canonical, v.strip().lower()) >= 80
                for v in vals[1:]
            )
        else:
            agreed = all(v.strip().lower() == canonical for v in vals[1:])

        agreement[key] = FieldAgreement(
            field_name=field_name,
            agreed=agreed,
            values=values,
        )

    return agreement


async def run_compare(
    pipeline,
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
    """Run the review for each model in *models* in parallel.

    Validates that models has 2–5 unique entries. Deduplicates with a warning.
    Article acquisition runs once; LLM steps run per model.
    """
    # Deduplicate model list, preserving order
    seen: set[str] = set()
    unique_models: list[str] = []
    for m in models:
        if m in seen:
            warnings.warn(
                f"Duplicate model '{m}' in compare_models; ignoring extra occurrence.",
                UserWarning,
                stacklevel=2,
            )
        else:
            seen.add(m)
            unique_models.append(m)

    if len(unique_models) < 2:
        raise ValueError("run_compare requires at least 2 unique model names.")
    if len(unique_models) > 5:
        raise ValueError("run_compare supports at most 5 models per run.")

    def up(msg: str) -> None:
        pipeline.log(msg)
        if progress_callback:
            progress_callback(msg)

    up(f"[compare] Starting compare run for {len(unique_models)} models: {unique_models}")

    # ── Step 1–6: Shared article acquisition ──
    up("[compare] Acquiring articles (shared across all models)...")
    acq = await pipeline._fetch_articles(
        progress_callback=progress_callback,
        auto_confirm=auto_confirm,
        confirm_callback=confirm_callback,
        max_plan_iterations=max_plan_iterations,
    )
    up(f"[compare] Acquisition complete: {len(acq.deduped)} articles after dedup.")

    # ── Steps 7–15: Per-model LLM pipeline (parallel) ──
    up("[compare] Running per-model LLM steps in parallel...")
    tasks = [
        _run_model_pipeline(
            pipeline,
            acq.deduped,
            acq.all_search_queries,
            acq.flow,
            model_name,
            progress_callback=progress_callback,
            data_items=data_items,
            output_synthesis_style=output_synthesis_style,
            assemble_timeout=assemble_timeout,
        )
        for model_name in unique_models
    ]
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    # Wrap results into ModelReviewRun entries
    model_results: list[ModelReviewRun] = []
    for model_name, raw in zip(unique_models, raw_results):
        if isinstance(raw, BaseException):
            logger.warning("Model '%s' run failed: %s", model_name, raw)
            up(f"[compare] {model_name}: FAILED — {raw}")
            model_results.append(ModelReviewRun(model_name=model_name, error=str(raw)))
        else:
            up(f"[compare] {model_name}: completed ({len(raw.included_articles or [])} included)")
            model_results.append(ModelReviewRun(model_name=model_name, result=raw))

    succeeded = [r for r in model_results if r.succeeded]

    # ── Field-level agreement ──
    field_agreement = _compute_field_agreement(model_results)
    up(f"[compare] Field agreement computed: {len(field_agreement)} fields compared.")

    # ── Consensus synthesis (LLM, only if ≥2 succeeded) ──
    consensus_text = _FALLBACK_CONSENSUS
    synthesis_divergences: list[SynthesisDivergence] = []

    if len(succeeded) >= 2:
        try:
            up("[compare] Running consensus synthesis agent...")
            _consensus_model = consensus_model or unique_models[0]
            consensus_deps = AgentDeps(
                protocol=pipeline.protocol,
                api_key=pipeline.deps.api_key,
                model_name=_consensus_model,
            )
            syntheses = {
                r.model_name: (r.result.synthesis_text or "")
                for r in succeeded
                if r.result
            }
            consensus_output = await run_consensus_synthesis(syntheses, consensus_deps)
            consensus_text = consensus_output.consensus_text
            synthesis_divergences = consensus_output.divergences
            up(f"[compare] Consensus synthesis complete ({len(synthesis_divergences)} divergences).")
        except Exception as exc:
            logger.warning("Consensus synthesis failed: %s", exc)
            consensus_text = f"Consensus synthesis failed: {exc}"

    merged = MergedReviewResult(
        consensus_synthesis=consensus_text,
        field_agreement=field_agreement,
        synthesis_divergences=synthesis_divergences,
    )

    return CompareReviewResult(
        protocol=pipeline.protocol,
        compare_models=unique_models,
        model_results=model_results,
        merged=merged,
    )
