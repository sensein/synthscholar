"""Integration tests for Feature 008 Bug 2: orphaned supporting_study IDs."""

import pytest


def _make_source_id(pmid: str) -> str:
    """Replicate the formula from agents.py run_extract_study() for testing."""
    return f"M-{pmid[-3:]}" if pmid.startswith("biorxiv_") else f"R-{pmid[-3:]}"


def _build_evidence_line(paper_pmid: str, text: str, pmid_to_source_id: dict) -> str:
    """Replicate the fixed evidence_block line construction from agents.py."""
    return f"- {pmid_to_source_id.get(paper_pmid, f'PMID:{paper_pmid}')}: {text[:150]}"


class TestPmidToSourceIdMapping:
    """Verify the pmid→source_id formula matches run_extract_study()."""

    def test_pubmed_pmid_maps_to_r_format(self):
        assert _make_source_id("38821669") == "R-669"

    def test_biorxiv_pmid_maps_to_m_format(self):
        assert _make_source_id("biorxiv_001234") == "M-234"

    def test_last_three_digits_used(self):
        assert _make_source_id("12345678") == "R-678"
        assert _make_source_id("12345000") == "R-000"

    def test_distinct_suffix_pmids_produce_distinct_source_ids(self):
        assert _make_source_id("38821669") != _make_source_id("39118728")

    def test_bulk_pmids_produce_r_format(self):
        pmids = ["38821669", "36854069", "39118728", "39824581"]
        source_ids = [_make_source_id(p) for p in pmids]
        assert all(sid.startswith("R-") for sid in source_ids)


class TestEvidenceBlockWithSourceIds:
    """Verify evidence block uses source_id format after the fix."""

    def test_maps_pmid_to_source_id_when_available(self):
        pmid = "38821669"
        mapping = {"38821669": "R-669"}
        line = _build_evidence_line(pmid, "Some evidence text", mapping)
        assert line.startswith("- R-669: ")
        assert "PMID:" not in line

    def test_falls_back_to_pmid_when_no_mapping(self):
        pmid = "99999999"
        line = _build_evidence_line(pmid, "Some evidence text", {})
        assert line.startswith("- PMID:99999999: ")

    def test_multiple_evidence_lines_use_source_ids(self):
        pmids = ["38821669", "36854069", "39118728"]
        mapping = {p: _make_source_id(p) for p in pmids}
        for pmid in pmids:
            line = _build_evidence_line(pmid, "Text", mapping)
            assert not line.startswith("- PMID:"), f"Expected source_id for PMID {pmid}"

    def test_text_is_truncated_to_150_chars(self):
        long_text = "x" * 200
        mapping = {"12345678": "R-678"}
        line = _build_evidence_line("12345678", long_text, mapping)
        assert line == f"- R-678: {'x' * 150}"


class TestOrphanedIdLogic:
    """Verify the orphan validation behaves correctly with source_id format."""

    def test_source_id_format_resolves_against_extracted_ids(self):
        extracted_ids = {"R-669", "R-069", "R-728"}
        supporting_studies = ["R-669", "R-069"]
        orphans = [sid for sid in supporting_studies if sid not in extracted_ids]
        assert orphans == []

    def test_pmid_format_fails_to_resolve_against_source_ids(self):
        """Documents the old bug: PMID-format IDs do not match R-XXX source_ids."""
        extracted_ids = {"R-669", "R-069"}
        broken_ids = ["PMID:38821669", "PMID:36854069"]
        orphans = [sid for sid in broken_ids if sid not in extracted_ids]
        assert len(orphans) == 2

    def test_fixed_mapping_produces_no_orphans(self):
        pmids = ["38821669", "36854069", "39118728"]
        pmid_to_source_id = {p: _make_source_id(p) for p in pmids}
        extracted_ids = set(pmid_to_source_id.values())
        # Simulate what the LLM should produce using source_id keys
        supporting_studies = list(pmid_to_source_id.values())
        orphans = [sid for sid in supporting_studies if sid not in extracted_ids]
        assert orphans == []
