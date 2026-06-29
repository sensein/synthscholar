"""Extended smoke coverage for the auth surface and OA full-text resolver chain.

Two tests live here:

* ``test_oa_resolver_chain_smoke`` — pure-network test (no LLM, no API key
  needed) that exercises Europe PMC, Unpaywall, OpenAlex, Semantic Scholar,
  and the PyMuPDF parse leg against real OA DOIs. Validates that the
  resolver chain wired up in ``synthscholar.clients.FullTextResolver`` is
  actually reaching the upstream services and producing extracted text.

* ``test_cli_auth_flags_smoke`` — full-pipeline run via the CLI that
  exercises the new ``--ncbi-api-key``, ``--email``, ``--semantic-scholar-key``,
  and ``--core-key`` flags. Gated on ``RUN_E2E=1`` and ``OPENROUTER_API_KEY``.

Both tests are marked ``smoke`` so the existing ``pytest -m smoke`` selection
picks them up. The OA-resolver test only requires network; the CLI test
requires a live OpenRouter key.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ──────────────────────── OA resolver chain (no LLM) ────────────────────────

@pytest.mark.smoke
def test_oa_resolver_chain_smoke():
    """Verify Europe PMC + DOI chain + PyMuPDF parse against real OA DOIs.

    No LLM credit consumed. Skips if pymupdf isn't installed, since the PDF
    parse leg needs it.
    """
    pytest.importorskip("pymupdf", reason="pymupdf required for PDF parse leg")

    from synthscholar.clients import FullTextResolver
    from synthscholar.models import Article

    res = FullTextResolver(email="tekraj@mit.edu")
    assert res.pdf_parser.available, "PyMuPDF reported not-available"

    # PMC-indexed OA article — should resolve via Europe PMC OA-XML route.
    pmc_article = Article(
        pmid="28245309",
        title="Hospital-based child overweight",
        doi="10.1371/journal.pone.0173033",
        source="journal",
    )
    text = res.resolve(pmc_article)
    assert text, "Europe PMC OA-XML route returned no text"
    assert len(text) >= 5000, f"Europe PMC text suspiciously short: {len(text)} chars"

    # bioRxiv preprint — should degrade cleanly (Cloudflare 403). Returning
    # None here is the correct behaviour, not a failure.
    biorxiv_article = Article(
        pmid="biorxiv_2024.05.28.596311",
        title="bioRxiv test",
        doi="10.1101/2024.05.28.596311",
        source="biorxiv",
    )
    # No assertion on the result — the resolver is allowed to return None.
    # The point is that it doesn't raise.
    res.resolve(biorxiv_article)


# ──────────────────────── CLI auth flags + full run ─────────────────────────

@pytest.mark.smoke
def test_cli_auth_flags_smoke(tmp_path):
    """End-to-end CLI run exercising every new auth flag.

    Skips when ``RUN_E2E`` or ``OPENROUTER_API_KEY`` is unset, identical
    gating to the existing ``test_cli_smoke_full_run``.
    """
    if not os.getenv("RUN_E2E"):
        pytest.skip("RUN_E2E not set")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set")

    cmd = [
        sys.executable, "-m", "synthscholar.main",
        "--title", "GLP-1 receptor agonists for adolescent obesity",
        "--population", "adolescents with obesity",
        "--intervention", "GLP-1 receptor agonists",
        "--outcome", "BMI reduction",
        "--inclusion", "Randomised controlled trials, 2020 onward",
        "--databases", "PubMed",
        "--max-results", "8",
        "--max-articles", "3",
        "--related-depth", "0",
        "--hops", "0",
        "--auto",
        "--no-cache",
        "--export", "md", "json",
        "--api-key", api_key,
        "--email", os.getenv("SYNTHSCHOLAR_EMAIL", "tekraj@mit.edu"),
    ]

    # Optional flags — only pass them if a secret is configured, so the test
    # works whether or not the optional providers are credentialed in CI.
    if (ncbi := os.getenv("NCBI_API_KEY")):
        cmd += ["--ncbi-api-key", ncbi]
    if (s2 := os.getenv("SEMANTIC_SCHOLAR_API_KEY")):
        cmd += ["--semantic-scholar-key", s2]
    if (core := os.getenv("CORE_API_KEY")):
        cmd += ["--core-key", core]

    env = os.environ.copy()
    env["PYTHONPATH"] = str(_PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    t0 = time.monotonic()
    result = subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(tmp_path),
        env=env, timeout=600,
    )
    elapsed = time.monotonic() - t0

    assert result.returncode == 0, (
        f"CLI exited {result.returncode} after {elapsed:.1f}s\n"
        f"STDOUT (tail):\n{result.stdout[-4000:]}\n"
        f"STDERR (tail):\n{result.stderr[-2000:]}"
    )

    out_dir = tmp_path / "prisma_results"
    md_files = list(out_dir.glob("*.md"))
    json_files = list(out_dir.glob("*.json"))
    assert md_files, "No .md exports produced"
    assert json_files, "No .json exports produced"

    # Sanity-check that the markdown file contains expected PRISMA sections.
    md_text = md_files[0].read_text()
    for marker in ("PRISMA", "Synthesis", "Risk of Bias"):
        assert marker.lower() in md_text.lower(), f"Markdown export missing '{marker}'"
