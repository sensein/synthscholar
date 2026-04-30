"""Provenance capture for SynthScholar runs.

Records *how* a review was produced — distinguishing zero-shot phases from
iterative ones (human feedback, fallback cascades, citation-hop expansion,
map-reduce). Three primary collectors:

* :class:`ProvenanceCollector` — per-run accumulator passed via
  :class:`synthscholar.agents.AgentDeps` so any agent call site can append.
* :func:`run_traced` — wraps ``agent.run()`` and writes one
  :class:`synthscholar.models.AgentInvocation` per invocation.
* :func:`build_run_configuration` — snapshots the user's CLI / kwargs /
  protocol / env-var presence at the start of a run.

API keys are NEVER stored — only their **presence** (bool) is recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from .models import (
    AgentInvocation,
    IterationMode,
    PlanIteration,
    RunConfiguration,
    SearchIteration,
)

# Env vars whose *presence* (not value) we record on every run.
TRACKED_ENV_VARS: tuple[str, ...] = (
    "OPENROUTER_API_KEY",
    "NCBI_API_KEY",
    "SYNTHSCHOLAR_EMAIL",
    "SEMANTIC_SCHOLAR_API_KEY",
    "CORE_API_KEY",
    "PRISMA_PG_DSN",
)

# Maximum characters of a captured prompt to keep on the in-memory result
# (the full text is still stored verbatim — this is just the safety cap so
# a 1 MB context block doesn't bloat every JSON export).
PROMPT_SNAPSHOT_CAP = 8000


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _package_version() -> str:
    try:
        from importlib.metadata import version
        return version("synthscholar")
    except Exception:
        return ""


def _jsonable(d: dict) -> dict:
    """Coerce a dict into something safely JSON-serialisable (repr fallback)."""
    out: dict = {}
    for k, v in d.items():
        try:
            json.dumps(v)
            out[str(k)] = v
        except (TypeError, ValueError):
            out[str(k)] = repr(v)
    return out


# ── Phase classification ───────────────────────────────────────────────
#
# Map a step_name → its default IterationMode. This is used by the
# Markdown exporter when rendering the "iterative vs zero-shot" summary,
# even when only a single invocation exists for that step.

PHASE_DEFAULT_MODE: dict[str, IterationMode] = {
    "search_strategy_generation": "iterative_with_human_feedback",
    "screening": "zero_shot",
    "evidence_extraction": "validated_against_source",
    "rob_assessment": "zero_shot",
    "data_charting": "zero_shot",
    "critical_appraisal": "zero_shot",
    "narrative_row": "zero_shot",
    "synthesis": "hierarchical_reduce",
    "thematic_synthesis": "hierarchical_reduce",
    "grade_assessment": "zero_shot",
    "bias_summary": "zero_shot",
    "limitations": "zero_shot",
    "introduction": "zero_shot",
    "conclusions": "zero_shot",
    "structured_abstract": "zero_shot",
    "abstract_section": "zero_shot",
    "introduction_section": "zero_shot",
    "discussion_section": "zero_shot",
    "conclusion_section": "zero_shot",
    "quantitative_analysis": "zero_shot",
    "grounding_validation": "validated_against_source",
    "search_synthesis": "hierarchical_reduce",
}


# ── Collector ──────────────────────────────────────────────────────────


class ProvenanceCollector:
    """Per-run accumulator. Threaded through AgentDeps.provenance."""

    def __init__(self) -> None:
        self.run_configuration: Optional[RunConfiguration] = None
        self.plan_iterations: list[PlanIteration] = []
        self.agent_invocations: list[AgentInvocation] = []
        self.search_iterations: list[SearchIteration] = []

    def record_invocation(self, inv: AgentInvocation) -> None:
        self.agent_invocations.append(inv)

    def record_plan_iteration(self, p: PlanIteration) -> None:
        self.plan_iterations.append(p)

    def record_search_iteration(self, s: SearchIteration) -> None:
        self.search_iterations.append(s)

    def stamp(self, result) -> None:
        """Attach this collector's contents to a PRISMAReviewResult."""
        result.run_configuration = self.run_configuration
        result.plan_iterations = list(self.plan_iterations)
        result.agent_invocations = list(self.agent_invocations)
        result.search_iterations = list(self.search_iterations)


# ── Run-configuration builder ──────────────────────────────────────────


def build_run_configuration(
    *,
    protocol,
    review_id: str,
    model_name: str,
    pipeline_kwargs: dict,
) -> RunConfiguration:
    """Snapshot the user's full configuration at the start of a run.

    API keys are not stored — only `env_vars_present` (bool) is recorded.
    """
    return RunConfiguration(
        started_at=_utcnow(),
        review_id=review_id,
        model_name=model_name,
        protocol_snapshot=_jsonable(protocol.model_dump(mode="json")),
        pipeline_kwargs=_jsonable(pipeline_kwargs),
        env_vars_present={k: bool(os.environ.get(k)) for k in TRACKED_ENV_VARS},
        cli_invocation=" ".join(sys.argv) if hasattr(sys, "argv") else "",
        package_version=_package_version(),
    )


# ── Tool-call summary ──────────────────────────────────────────────────


def _system_prompt_text(agent: Any) -> str:
    sp = getattr(agent, "_system_prompts", None) or ()
    return "\n\n".join(str(s) for s in sp)


def _summarize_tool_calls(messages: list, retries: int) -> tuple[int, str]:
    """One-line summary: '2 tool call(s): pubmed×2; 1 retry'."""
    tool_names: list[str] = []
    for m in messages or []:
        for part in getattr(m, "parts", []) or []:
            tn = getattr(part, "tool_name", None)
            if tn:
                tool_names.append(str(tn))
    n = len(tool_names)
    suffix = f"; {retries} retry" if retries == 1 else (f"; {retries} retries" if retries > 1 else "")
    if n == 0:
        return 0, f"no tool calls{suffix}".strip("; ").strip() or "no tool calls"
    counts = Counter(tool_names).most_common(3)
    bits = ", ".join(f"{name}×{c}" if c > 1 else name for name, c in counts)
    return n, f"{n} tool call(s): {bits}{suffix}"


# ── The capture wrapper ────────────────────────────────────────────────


async def run_traced(
    agent,
    prompt: str,
    *,
    deps,
    model,
    step_name: str,
    iteration_mode: IterationMode = "zero_shot",
    target_pmid: str = "",
    target_outcome: str = "",
    batch_index: int = -1,
    output_type=None,
):
    """Wrap ``agent.run(prompt, deps=..., model=...)`` with provenance capture.

    Always records an :class:`AgentInvocation` on ``deps.provenance`` (when
    that attribute exists). Re-raises any exception unchanged after
    recording the failure.
    """
    started_at = _utcnow()
    t0 = time.monotonic()

    static_sp = _system_prompt_text(agent)
    agent_name = getattr(agent, "name", "") or type(agent).__name__
    out_t = output_type if output_type is not None else getattr(agent, "output_type", None)
    output_type_name = getattr(out_t, "__name__", "") if out_t is not None else ""

    succeeded = True
    err_msg = ""
    requests = 0
    tokens_in = tokens_out = tokens_total = 0
    cache_r = cache_w = 0
    n_tool_calls = 0
    summary = ""
    result = None

    try:
        kwargs: dict = {"deps": deps, "model": model}
        if output_type is not None:
            kwargs["output_type"] = output_type
        result = await agent.run(prompt, **kwargs)
        try:
            usage = result.usage()
            tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
            tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
            tokens_total = int(getattr(usage, "total_tokens", 0) or 0)
            cache_r = int(getattr(usage, "cache_read_tokens", 0) or 0)
            cache_w = int(getattr(usage, "cache_write_tokens", 0) or 0)
            requests = int(getattr(usage, "requests", 0) or 0)
        except Exception:
            pass
        try:
            messages = result.all_messages() or []
            retries = max(0, requests - 1) if requests > 1 else 0
            n_tool_calls, summary = _summarize_tool_calls(messages, retries)
        except Exception:
            n_tool_calls, summary = 0, ""
    except Exception as e:
        succeeded = False
        err_msg = f"{type(e).__name__}: {e}"
        raise
    finally:
        duration_ms = (time.monotonic() - t0) * 1000.0
        prov = getattr(deps, "provenance", None)
        # Only record on real ProvenanceCollector instances (not MagicMocks
        # from tests, not None when caller hasn't opted in).
        if isinstance(prov, ProvenanceCollector):
            prompt_text = prompt if isinstance(prompt, str) else str(prompt)
            _model_name = getattr(deps, "model_name", "") or ""
            if not isinstance(_model_name, str):
                _model_name = str(_model_name)
            inv = AgentInvocation(
                agent_name=agent_name,
                step_name=step_name,
                iteration_mode=iteration_mode,
                model_name=_model_name,
                started_at=started_at,
                duration_ms=duration_ms,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                total_tokens=tokens_total,
                cache_read_tokens=cache_r,
                cache_write_tokens=cache_w,
                requests=requests,
                tool_calls_count=n_tool_calls,
                tool_call_summary=summary,
                target_pmid=target_pmid,
                target_outcome=target_outcome,
                batch_index=batch_index,
                system_prompt_snapshot=static_sp[:PROMPT_SNAPSHOT_CAP],
                prompt_snapshot=prompt_text[:PROMPT_SNAPSHOT_CAP],
                output_type=output_type_name,
                succeeded=succeeded,
                error_message=err_msg[:500],
            )
            prov.record_invocation(inv)

    return result


# ── SHA-256 content hash ───────────────────────────────────────────────


def content_sha256(text: str) -> str:
    """Hex SHA-256 of utf-8 encoded text (empty string for empty input)."""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
