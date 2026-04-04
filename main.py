#!/usr/bin/env python3
"""
PRISMA 2020 Systematic Review Agent — Standalone CLI

Usage:
    # Set your API key
    export OPENROUTER_API_KEY="sk-or-v1-..."

    # Quick review with defaults
    python main.py --title "CRISPR gene therapy efficacy" \
                   --inclusion "Clinical trials, human subjects" \
                   --exclusion "Animal-only studies, reviews"

    # Full PICO specification
    python main.py --title "GLP-1 agonists for type 2 diabetes" \
                   --objective "Evaluate efficacy of GLP-1 RAs vs placebo" \
                   --population "Adults with T2DM" \
                   --intervention "GLP-1 receptor agonists" \
                   --comparison "Placebo or standard care" \
                   --outcome "HbA1c reduction, weight change" \
                   --inclusion "RCTs, English, 2019-2024" \
                   --exclusion "Case reports, editorials" \
                   --model "anthropic/claude-sonnet-4" \
                   --max-results 30 \
                   --hops 2 \
                   --export md json bib

    # Interactive mode (prompts for input)
    python main.py --interactive
"""

import os
import sys
import asyncio
import argparse
from pathlib import Path
from datetime import datetime

try:
    # Installed package (pip install prisma-review-agent)
    from prisma_review_agent import __version__
    from prisma_review_agent.models import ReviewProtocol, RoBTool
    from prisma_review_agent.pipeline import PRISMAReviewPipeline
    from prisma_review_agent.export import to_markdown, to_bibtex, to_json
except ImportError:
    # Running directly from source: python main.py
    from prisma_review_agent import __version__  # type: ignore[no-redef]
    from models import ReviewProtocol, RoBTool  # type: ignore[no-redef]
    from pipeline import PRISMAReviewPipeline  # type: ignore[no-redef]
    from export import to_markdown, to_bibtex, to_json  # type: ignore[no-redef]

ROB_TOOL_CHOICES = [t.value for t in RoBTool]


OUTPUT_DIR = Path("prisma_results")


def get_api_key() -> str:
    """Get OpenRouter API key from environment."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("ERROR: Set OPENROUTER_API_KEY environment variable.")
        print("  export OPENROUTER_API_KEY='sk-or-v1-...'")
        sys.exit(1)
    return key


def build_protocol_from_args(args: argparse.Namespace) -> ReviewProtocol:
    """Build ReviewProtocol from CLI arguments."""
    return ReviewProtocol(
        title=args.title,
        objective=args.objective or args.title,
        pico_population=args.population or "",
        pico_intervention=args.intervention or "",
        pico_comparison=args.comparison or "",
        pico_outcome=args.outcome or "",
        inclusion_criteria=args.inclusion or "",
        exclusion_criteria=args.exclusion or "",
        databases=args.databases,
        date_range_start=args.date_start or "",
        date_range_end=args.date_end or "",
        max_hops=args.hops,
        registration_number=args.registration or "",
        rob_tool=RoBTool(args.rob_tool),
    )


def build_protocol_interactive() -> ReviewProtocol:
    """Build protocol interactively via prompts."""
    print("\n" + "=" * 60)
    print("  PRISMA 2020 Systematic Review — Protocol Setup")
    print("=" * 60 + "\n")

    title = input("Review title: ").strip()
    objective = input("Objective/research question: ").strip() or title

    print("\n--- PICO Framework ---")
    population = input("Population: ").strip()
    intervention = input("Intervention: ").strip()
    comparison = input("Comparison: ").strip()
    outcome = input("Outcome: ").strip()

    print("\n--- Criteria ---")
    inclusion = input("Inclusion criteria: ").strip()
    exclusion = input("Exclusion criteria: ").strip()

    print("\n--- Search settings ---")
    hops = input("Citation hops (0-4) [1]: ").strip()
    hops = int(hops) if hops.isdigit() else 1
    print("  Available RoB tools:")
    for i, tool in enumerate(RoBTool, 1):
        print(f"    {i}. {tool.value}")
    rob_choice = input(f"  Select RoB tool (1-{len(RoBTool)}) [1]: ").strip()
    rob_idx = int(rob_choice) - 1 if rob_choice.isdigit() else 0
    rob_tools_list = list(RoBTool)
    rob = rob_tools_list[min(rob_idx, len(rob_tools_list) - 1)]

    return ReviewProtocol(
        title=title,
        objective=objective,
        pico_population=population,
        pico_intervention=intervention,
        pico_comparison=comparison,
        pico_outcome=outcome,
        inclusion_criteria=inclusion,
        exclusion_criteria=exclusion,
        max_hops=hops,
        rob_tool=rob if isinstance(rob, RoBTool) else RoBTool(rob),
    )


def save_exports(result, formats: list[str]):
    """Save review results in requested formats."""
    OUTPUT_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = result.research_question[:40].replace(" ", "_").lower()
    base = f"{slug}_{ts}"

    saved = []
    if "md" in formats or "markdown" in formats:
        path = OUTPUT_DIR / f"{base}.md"
        path.write_text(to_markdown(result), encoding="utf-8")
        saved.append(str(path))

    if "json" in formats:
        path = OUTPUT_DIR / f"{base}.json"
        path.write_text(to_json(result), encoding="utf-8")
        saved.append(str(path))

    if "bib" in formats or "bibtex" in formats:
        path = OUTPUT_DIR / f"{base}.bib"
        path.write_text(to_bibtex(result), encoding="utf-8")
        saved.append(str(path))

    return saved


async def run_review(args: argparse.Namespace):
    """Execute the PRISMA review pipeline."""
    api_key = get_api_key()

    # Build protocol
    if args.interactive:
        protocol = build_protocol_interactive()
    else:
        if not args.title:
            print("ERROR: --title is required (or use --interactive)")
            sys.exit(1)
        protocol = build_protocol_from_args(args)

    # Data extraction items
    data_items = None
    if args.extract_data:
        data_items = [
            "Study design", "Sample size", "Population",
            "Intervention", "Comparator", "Primary outcomes",
            "Effect sizes", "Follow-up duration",
        ]

    print("\n" + "=" * 60)
    print(f"  Starting PRISMA Review: {protocol.title}")
    print(f"  Model: {args.model}")
    print(f"  Databases: {', '.join(protocol.databases)}")
    print(f"  Max results/query: {args.max_results}")
    print(f"  Citation hops: {protocol.max_hops}")
    print(f"  RoB tool: {protocol.rob_tool.value}")
    print("=" * 60 + "\n")

    pipeline = PRISMAReviewPipeline(
        api_key=api_key,
        model_name=args.model,
        ncbi_api_key=os.environ.get("NCBI_API_KEY", ""),
        protocol=protocol,
        enable_cache=not args.no_cache,
        max_per_query=args.max_results,
        related_depth=args.related_depth,
        biorxiv_days=args.biorxiv_days,
    )

    result = await pipeline.run(data_items=data_items)

    # Print summary
    f = result.flow
    print("\n" + "=" * 60)
    print("  PRISMA Flow Summary")
    print("=" * 60)
    print(f"  Identified:      {f.total_identified}")
    print(f"  After dedup:     {f.after_dedup}")
    print(f"  Screened:        {f.screened_title_abstract}")
    print(f"  Excluded (TA):   {f.excluded_title_abstract}")
    print(f"  FT assessed:     {f.assessed_eligibility}")
    print(f"  Excluded (FT):   {f.excluded_eligibility}")
    print(f"  INCLUDED:        {f.included_synthesis}")
    print("=" * 60)

    if result.included_articles:
        print(f"\nIncluded studies ({len(result.included_articles)}):")
        for a in result.included_articles:
            rob = a.risk_of_bias.overall.value if a.risk_of_bias else "?"
            print(f"  [{a.pmid}] {a.short_author} ({a.year}) — RoB: {rob}")

    if result.evidence_spans:
        print(f"\nTop evidence spans ({len(result.evidence_spans)}):")
        for e in result.evidence_spans[:5]:
            print(f"  [{e.paper_pmid}, {e.relevance_score:.2f}] {e.text[:100]}...")

    # Print synthesis preview
    if result.synthesis_text:
        print("\n" + "-" * 60)
        print("SYNTHESIS (first 500 chars):")
        print("-" * 60)
        print(result.synthesis_text[:500] + "...")

    # Export
    formats = args.export or ["md"]
    saved = save_exports(result, formats)
    if saved:
        print(f"\nExported to:")
        for p in saved:
            print(f"  {p}")

    return result


def main():
    parser = argparse.ArgumentParser(
        prog="prisma-review",
        description="PRISMA 2020 Systematic Review Agent (Pydantic AI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  prisma-review --title "CRISPR gene therapy" --inclusion "Clinical trials" --exclusion "Reviews"
  prisma-review --interactive
  prisma-review --title "ML drug discovery" --model google/gemini-2.5-pro --export md json bib

  # Or from source:
  python main.py --title "CRISPR gene therapy" --interactive
        """,
    )

    parser.add_argument("--version", "-V", action="version", version=f"%(prog)s {__version__}")

    # Protocol
    parser.add_argument("--title", "-t", type=str, default="",
                        help="Review title / research question")
    parser.add_argument("--objective", type=str, default="",
                        help="Detailed objective (defaults to title)")
    parser.add_argument("--population", type=str, default="")
    parser.add_argument("--intervention", type=str, default="")
    parser.add_argument("--comparison", type=str, default="")
    parser.add_argument("--outcome", type=str, default="")
    parser.add_argument("--inclusion", type=str, default="",
                        help="Inclusion criteria")
    parser.add_argument("--exclusion", type=str, default="",
                        help="Exclusion criteria")
    parser.add_argument("--registration", type=str, default="",
                        help="Registration number (e.g., PROSPERO)")

    # Search settings
    parser.add_argument("--model", "-m", type=str, default="anthropic/claude-sonnet-4",
                        help="OpenRouter model name")
    parser.add_argument("--databases", nargs="+", default=["PubMed", "bioRxiv"],
                        help="Databases to search")
    parser.add_argument("--max-results", type=int, default=20,
                        help="Max results per query")
    parser.add_argument("--related-depth", type=int, default=1,
                        help="Related article search depth")
    parser.add_argument("--hops", type=int, default=1,
                        help="Multi-hop citation depth (0-4)")
    parser.add_argument("--biorxiv-days", type=int, default=180,
                        help="bioRxiv lookback days")
    parser.add_argument("--date-start", type=str, default="",
                        help="Date range start (YYYY-MM-DD)")
    parser.add_argument("--date-end", type=str, default="",
                        help="Date range end (YYYY-MM-DD)")
    parser.add_argument("--rob-tool", type=str, default="RoB 2",
                        choices=ROB_TOOL_CHOICES,
                        help="Risk of bias assessment tool")

    # Pipeline options
    parser.add_argument("--no-cache", action="store_true",
                        help="Disable SQLite cache")
    parser.add_argument("--extract-data", action="store_true",
                        help="Enable per-study data extraction")

    # Output
    parser.add_argument("--export", "-e", nargs="+", default=["md"],
                        choices=["md", "markdown", "json", "bib", "bibtex"],
                        help="Export formats")

    # Mode
    parser.add_argument("--interactive", "-i", action="store_true",
                        help="Interactive protocol setup")

    args = parser.parse_args()
    asyncio.run(run_review(args))


if __name__ == "__main__":
    main()
