"""US1: CLI end-to-end tests using subprocess invocation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Project root — needed so the subprocess can import prisma_review_agent
_PROJECT_ROOT = Path(__file__).parent.parent.parent


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "prisma_review_agent.main"] + args
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        cmd, capture_output=True, text=True, cwd=str(cwd), env=env, timeout=120
    )


# ── Basic argument tests (no model required) ─────────────────────────────────

def test_cli_help_exits_zero(tmp_path):
    result = run_cli(["--help"], cwd=tmp_path)
    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "prisma" in combined or "usage" in combined or "systematic" in combined


def test_cli_invalid_no_title_exits_nonzero(tmp_path):
    result = run_cli(["--api-key", "dummy"], cwd=tmp_path)
    assert result.returncode != 0


# ── Mock pipeline tests using --model test ────────────────────────────────────

@pytest.mark.e2e
def test_cli_mock_run_exits_zero(tmp_path):
    result = run_cli(
        [
            "--title", "CRISPR base editing in sickle cell disease",
            "--api-key", "test-mock-key",
            "--model", "test",
            "--auto",
            "--export", "md", "json",
            "--max-results", "1",
            "--hops", "0",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, (
        f"CLI exited {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    output_files = list((tmp_path / "prisma_results").glob("*.md"))
    assert len(output_files) >= 1, "Expected at least one .md file in prisma_results/"


@pytest.mark.e2e
def test_cli_output_markdown_has_prisma_sections(tmp_path):
    result = run_cli(
        [
            "--title", "CRISPR base editing in sickle cell disease",
            "--api-key", "test-mock-key",
            "--model", "test",
            "--auto",
            "--export", "md",
            "--max-results", "1",
            "--hops", "0",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    md_files = list((tmp_path / "prisma_results").glob("*.md"))
    assert md_files, "No .md files produced"
    content = md_files[0].read_text()
    for section in ("Methods", "Results", "References"):
        assert section in content, f"'{section}' not found in markdown output"


@pytest.mark.e2e
def test_cli_output_json_is_valid(tmp_path):
    result = run_cli(
        [
            "--title", "CRISPR base editing in sickle cell disease",
            "--api-key", "test-mock-key",
            "--model", "test",
            "--auto",
            "--export", "json",
            "--max-results", "1",
            "--hops", "0",
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    json_files = list((tmp_path / "prisma_results").glob("*.json"))
    assert json_files, "No .json files produced"
    parsed = json.loads(json_files[0].read_text())
    assert "research_question" in parsed


# ── Smoke test (requires real API key) ───────────────────────────────────────

@pytest.mark.smoke
def test_cli_smoke_full_run(tmp_path):
    if not os.getenv("RUN_E2E"):
        pytest.skip("RUN_E2E not set")
    api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set")

    t0 = time.monotonic()
    result = run_cli(
        [
            "--title", "CRISPR base editing in sickle cell disease",
            "--api-key", api_key,
            "--auto",
            "--export", "md",
            "--max-results", "5",
            "--hops", "0",
        ],
        cwd=tmp_path,
    )
    elapsed = time.monotonic() - t0
    assert result.returncode == 0, (
        f"Smoke CLI exited {result.returncode} after {elapsed:.1f}s\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    md_files = list((tmp_path / "prisma_results").glob("*.md"))
    assert md_files, "No .md files produced in smoke run"
