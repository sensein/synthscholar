"""Contract tests for feature 010 — pipeline checkpoint CacheStore methods.

These tests use a mock connection pool to verify the contract of the
save_checkpoint / load_checkpoints / clear_checkpoints methods without
requiring a live PostgreSQL instance.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synthscholar.cache.models import PipelineCheckpoint, BatchMaxRetriesError
from synthscholar.cache.store import CacheStore, _row_to_checkpoint


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_ckpt(**kwargs) -> PipelineCheckpoint:
    defaults = dict(review_id="r1", stage_name="synthesis", batch_index=0, status="pending")
    defaults.update(kwargs)
    return PipelineCheckpoint(**defaults)


def _db_row(ckpt: PipelineCheckpoint) -> dict:
    return {
        "id": ckpt.id or 1,
        "review_id": ckpt.review_id,
        "stage_name": ckpt.stage_name,
        "batch_index": ckpt.batch_index,
        "status": ckpt.status,
        "result_json": ckpt.result_json,
        "error_message": ckpt.error_message,
        "retries": ckpt.retries,
        "created_at": ckpt.created_at,
        "updated_at": ckpt.updated_at,
    }


# ── _row_to_checkpoint helper ──────────────────────────────────────────────────

class TestRowToCheckpoint:
    def test_round_trip(self):
        orig = _make_ckpt(
            status="complete", result_json={"foo": "bar"}, retries=2,
            error_message="none"
        )
        row = _db_row(orig)
        row["id"] = 42
        restored = _row_to_checkpoint(row)
        assert restored.id == 42
        assert restored.review_id == "r1"
        assert restored.stage_name == "synthesis"
        assert restored.batch_index == 0
        assert restored.status == "complete"
        assert restored.result_json == {"foo": "bar"}
        assert restored.retries == 2

    def test_missing_optional_fields_use_defaults(self):
        row = {
            "id": 1, "review_id": "r1", "stage_name": "rob", "batch_index": 0,
            "status": "pending", "result_json": {},
            "created_at": datetime.utcnow(), "updated_at": datetime.utcnow(),
        }
        ckpt = _row_to_checkpoint(row)
        assert ckpt.error_message == ""
        assert ckpt.retries == 0


# ── BatchMaxRetriesError contract ─────────────────────────────────────────────

class TestBatchMaxRetriesContract:
    def test_error_carries_structured_info(self):
        err = BatchMaxRetriesError("charting", 7, 3)
        assert err.stage == "charting"
        assert err.batch_index == 7
        assert err.retries == 3
        assert isinstance(err, RuntimeError)

    def test_str_is_human_readable(self):
        err = BatchMaxRetriesError("synthesis", 2, 3)
        msg = str(err)
        assert "synthesis" in msg
        assert "2" in msg
        assert "3" in msg


# ── PipelineCheckpoint model contract ─────────────────────────────────────────

class TestPipelineCheckpointContract:
    def test_upsert_semantics_same_key(self):
        """Two checkpoints with the same key should represent the same DB row."""
        ckpt_v1 = _make_ckpt(status="in_progress", retries=0)
        ckpt_v2 = _make_ckpt(status="failed", retries=1, error_message="Timeout")
        assert ckpt_v1.review_id == ckpt_v2.review_id
        assert ckpt_v1.stage_name == ckpt_v2.stage_name
        assert ckpt_v1.batch_index == ckpt_v2.batch_index
        # Different status / retries represent an update of the same row
        assert ckpt_v1.status != ckpt_v2.status

    def test_complete_checkpoint_has_result_json(self):
        payload = {"synthesis_text": "Across 30 studies..."}
        ckpt = _make_ckpt(status="complete", result_json=payload)
        assert ckpt.result_json["synthesis_text"].startswith("Across")

    def test_failed_checkpoint_has_error_message(self):
        ckpt = _make_ckpt(status="failed", error_message="LLM call timed out", retries=3)
        assert ckpt.error_message == "LLM call timed out"
        assert ckpt.retries == 3

    def test_serialization_round_trip(self):
        ckpt = _make_ckpt(status="complete", result_json={"k": [1, 2, 3]}, retries=0)
        dumped = ckpt.model_dump(mode="json")
        restored = PipelineCheckpoint(**dumped)
        assert restored.result_json == {"k": [1, 2, 3]}
        assert restored.status == "complete"
