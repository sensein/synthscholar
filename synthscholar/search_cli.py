"""``synthscholar-search`` console script.

Usage::

    synthscholar-search literature "CRISPR sickle cell" --semantic --top 10 --summarize
    synthscholar-search reviews    "obesity treatment"   --keyword  --top 5  --summarize

Two subcommands:

* ``literature`` — searches :class:`synthscholar.cache.article_store.ArticleStore`
  (every article ever fetched into the Postgres-backed corpus).
* ``reviews`` — searches :class:`synthscholar.cache.store.CacheStore`
  (every review previously executed and stored in ``review_cache``).

Each subcommand exposes three modes:

* ``--keyword``  — title + abstract + full-text FTS via Postgres tsvector
  (default for ``literature``; fastest, no LLM cost, no embedding required).
* ``--by-title`` — title-favouring FTS variant.
* ``--semantic`` — pgvector cosine-similarity search using
  ``sentence-transformers/all-MiniLM-L6-v2``. Requires migration 004 and the
  ``[semantic]`` extra.

Add ``--summarize`` to either mode to feed the top-K results through the
``search_synthesis_agent`` and produce a stratified summary (e.g. by
condition / disorder / population) using your configured LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Optional


def _resolve_dsn(cli_value: str) -> str:
    dsn = cli_value or os.environ.get("PRISMA_PG_DSN", "")
    if not dsn:
        print(
            "ERROR: no PostgreSQL DSN supplied. Pass --pg-dsn or set PRISMA_PG_DSN.",
            file=sys.stderr,
        )
        sys.exit(1)
    return dsn


def _print_articles(arts, mode: str, json_out: bool) -> None:
    if json_out:
        out = [
            {
                "pmid": a.pmid,
                "title": a.title,
                "year": a.year,
                "doi": a.doi,
                "journal": a.journal,
                "source": a.source,
            }
            for a in arts
        ]
        print(json.dumps(out, indent=2))
        return

    if not arts:
        print(f"No articles matched the {mode} query.")
        return
    for i, a in enumerate(arts, 1):
        print(f"  {i:>2}. [PMID:{a.pmid}] {a.title[:90]}")
        meta = " ".join(filter(None, [a.year, a.journal[:40], (a.doi[:30] if a.doi else "")]))
        if meta:
            print(f"      {meta}")


def _print_synthesis(syn, json_out: bool) -> None:
    if json_out:
        print(syn.model_dump_json(indent=2))
        return
    print()
    print("=== Search Synthesis ===")
    print(f"Query:      {syn.query}")
    print(f"Articles:   {syn.n_articles_synthesized}")
    print(f"Overview:   {syn.overview}")
    print()
    for g in syn.groups:
        print(f"  • {g.label} (n={g.n_studies})")
        print(f"      {g.aggregate_finding}")
        if g.representative_pmids:
            print(f"      pmids: {', '.join(g.representative_pmids)}")
        if g.caveats:
            print(f"      caveats: {g.caveats}")
    if syn.overall_caveats:
        print()
        print(f"Cross-group caveats: {syn.overall_caveats}")


# ── literature subcommand ────────────────────────────────────────────────────


async def _do_literature(args: argparse.Namespace) -> int:
    from .cache.article_store import ArticleStore

    dsn = _resolve_dsn(args.pg_dsn)
    store = ArticleStore(dsn=dsn)
    await store.connect()
    try:
        if args.semantic:
            arts = await store.search_semantic(args.query, limit=args.top)
            mode = "semantic"
        elif args.by_title:
            arts = await store.search_by_title(args.query, limit=args.top)
            mode = "title-FTS"
        else:
            arts = await store.search_by_keyword(args.query, limit=args.top)
            mode = "keyword-FTS"

        if not args.json:
            print(f"Found {len(arts)} articles ({mode}):")
        _print_articles(arts, mode, args.json)

        if args.summarize and arts:
            from .agents import AgentDeps, run_search_synthesis
            from .models import ReviewProtocol

            api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                print(
                    "WARNING: --summarize requires OPENROUTER_API_KEY (or --api-key); "
                    "skipping synthesis.",
                    file=sys.stderr,
                )
            else:
                deps = AgentDeps(
                    protocol=ReviewProtocol(question=args.query),
                    api_key=api_key,
                    model_name=args.model,
                )
                synth = await run_search_synthesis(
                    args.query, arts, deps, top_k=args.summary_top,
                )
                _print_synthesis(synth, args.json)
    finally:
        await store.close()
    return 0


# ── reviews subcommand ──────────────────────────────────────────────────────


async def _do_reviews(args: argparse.Namespace) -> int:
    from .cache.store import CacheStore

    dsn = _resolve_dsn(args.pg_dsn)
    store = CacheStore(dsn=dsn)
    await store.connect()
    try:
        if args.semantic:
            entries = await store.search_reviews_semantic(
                args.query, limit=args.top, include_expired=args.include_expired,
            )
            mode = "semantic"
        else:
            entries = await store.search_reviews_keyword(
                args.query, limit=args.top, include_expired=args.include_expired,
            )
            mode = "keyword-FTS"

        if args.json:
            out = [
                {
                    "fingerprint": e.criteria_fingerprint,
                    "model_name": e.model_name,
                    "question": e.criteria_json.get("question") or e.criteria_json.get("title"),
                    "review_id": e.review_id,
                    "created_at": e.created_at.isoformat() if e.created_at else "",
                }
                for e in entries
            ]
            print(json.dumps(out, indent=2))
        else:
            print(f"Found {len(entries)} reviews ({mode}):")
            for i, e in enumerate(entries, 1):
                q = (e.criteria_json.get("question") or e.criteria_json.get("title") or "(no title)")[:90]
                print(f"  {i:>2}. {q}")
                print(f"      model={e.model_name}  fingerprint={e.criteria_fingerprint[:12]}…  id={e.review_id or '—'}")

        if args.summarize and entries:
            # Reduce each cached review to its synthesis_text + question and feed
            # through the search-synthesis agent as if they were articles, so the
            # output groups by topic/finding across reviews.
            from .agents import AgentDeps, run_search_synthesis
            from .models import Article, ReviewProtocol

            api_key = args.api_key or os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                print(
                    "WARNING: --summarize requires OPENROUTER_API_KEY (or --api-key); "
                    "skipping synthesis.",
                    file=sys.stderr,
                )
            else:
                pseudo_articles = [
                    Article(
                        pmid=f"review_{e.criteria_fingerprint[:12]}",
                        title=str(e.criteria_json.get("question") or e.criteria_json.get("title") or ""),
                        abstract=str(
                            (e.result_json or {}).get("synthesis_text") or
                            (e.result_json or {}).get("structured_abstract") or ""
                        )[:4000],
                        year=str(e.created_at.year) if e.created_at else "",
                        source="review_cache",
                    )
                    for e in entries
                ]
                deps = AgentDeps(
                    protocol=ReviewProtocol(question=args.query),
                    api_key=api_key,
                    model_name=args.model,
                )
                synth = await run_search_synthesis(
                    args.query, pseudo_articles, deps, top_k=args.summary_top,
                )
                _print_synthesis(synth, args.json)
    finally:
        await store.close()
    return 0


# ── argument parsing ────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="synthscholar-search",
        description="Search the persisted article corpus or past reviews.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Common args helper.
    def _add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("query", type=str, help="Free-text search query")
        sp.add_argument("--pg-dsn", type=str, default="",
                        help="PostgreSQL DSN (or set PRISMA_PG_DSN env var)")
        sp.add_argument("--top", type=int, default=20,
                        help="Max results to return (default 20)")
        sp.add_argument("--summarize", action="store_true",
                        help="Feed top results through the search-synthesis "
                             "agent (one LLM call) for a stratified summary.")
        sp.add_argument("--summary-top", type=int, default=15,
                        help="Articles fed to the synthesis agent when "
                             "--summarize is set (default 15)")
        sp.add_argument("--api-key", type=str, default="",
                        help="OpenRouter API key (or OPENROUTER_API_KEY env). "
                             "Only required when --summarize is used.")
        sp.add_argument("--model", type=str, default="anthropic/claude-sonnet-4",
                        help="LLM model name for --summarize (default: claude-sonnet-4)")
        sp.add_argument("--json", action="store_true",
                        help="Emit JSON instead of human-readable output.")

    lit = sub.add_parser(
        "literature",
        help="Search articles previously fetched into the Postgres article_store.",
    )
    _add_common(lit)
    mode = lit.add_mutually_exclusive_group()
    mode.add_argument("--by-title", action="store_true",
                      help="Title-favouring lexical FTS (default: title+abstract+fulltext)")
    mode.add_argument("--semantic", action="store_true",
                      help="Vector semantic search (requires migration 004 + [semantic] extra)")

    rev = sub.add_parser(
        "reviews",
        help="Search past reviews stored in review_cache.",
    )
    _add_common(rev)
    mode2 = rev.add_mutually_exclusive_group()
    mode2.add_argument("--semantic", action="store_true",
                       help="Vector semantic search (requires migration 004 + [semantic] extra)")
    rev.add_argument("--include-expired", action="store_true",
                     help="Include reviews whose cache TTL has passed")

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.cmd == "literature":
        return asyncio.run(_do_literature(args))
    if args.cmd == "reviews":
        return asyncio.run(_do_reviews(args))
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
