"""Criteria fingerprinting and weighted similarity scoring."""

from __future__ import annotations

import hashlib
import json
from typing import Any

try:
    from rapidfuzz import fuzz as _fuzz
    _RAPIDFUZZ = True
except ImportError:
    _RAPIDFUZZ = False

from .models import SimilarityConfig


def compute_fingerprint(criteria: dict[str, Any] | object, model_name: str) -> str:
    """Return a SHA-256 hex fingerprint of canonical review criteria.

    The fingerprint is deterministic: whitespace-normalised, lower-cased,
    list fields sorted.  The model name is included so model-version changes
    always produce a different fingerprint (cache miss).
    """
    if not isinstance(criteria, dict):
        # Accept a ReviewProtocol or any model_dump()-able object
        try:
            criteria = criteria.model_dump()  # type: ignore[union-attr]
        except AttributeError:
            criteria = dict(criteria)  # type: ignore[call-overload]

    canonical: dict[str, Any] = {
        "title":              _norm(criteria.get("title", "")),
        "objective":          _norm(criteria.get("objective", "")),
        "inclusion_criteria": _norm(criteria.get("inclusion_criteria", "")),
        "exclusion_criteria": _norm(criteria.get("exclusion_criteria", "")),
        "pico_population":    _norm(criteria.get("pico_population", "")),
        "pico_intervention":  _norm(criteria.get("pico_intervention", "")),
        "pico_comparison":    _norm(criteria.get("pico_comparison", "")),
        "pico_outcome":       _norm(criteria.get("pico_outcome", "")),
        "databases":          sorted(_norm(d) for d in criteria.get("databases", [])),
        "date_range_start":   _norm(criteria.get("date_range_start", "")),
        "date_range_end":     _norm(criteria.get("date_range_end", "")),
        "rob_tool":           _norm(str(criteria.get("rob_tool", ""))),
        "model_name":         _norm(model_name),
    }
    blob = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def compute_similarity(
    incoming: dict[str, Any],
    cached: dict[str, Any],
    config: SimilarityConfig,
) -> float:
    """Return a weighted aggregate similarity score in [0.0, 1.0].

    Each text field is scored with rapidfuzz token_set_ratio (handles
    word-order variation).  Exact-match fields (databases, rob_tool,
    date_range) contribute 1.0 or 0.0.

    Falls back to 0.0 for all fields if rapidfuzz is unavailable.
    """
    if not _RAPIDFUZZ:
        return 0.0

    weights = config.field_weights
    total_score = 0.0

    # Text fields — fuzzy
    for field in ("title", "objective", "inclusion_criteria", "exclusion_criteria",
                  "pico_population", "pico_intervention", "pico_comparison", "pico_outcome"):
        w = weights.get(field, 0.0)
        if w == 0.0:
            continue
        a = _norm(incoming.get(field, ""))
        b = _norm(cached.get(field, ""))
        if not a and not b:
            total_score += w  # both empty = identical
        elif not a or not b:
            pass  # one empty, one not = 0
        else:
            total_score += w * _fuzz.token_set_ratio(a, b) / 100.0

    # Databases — Jaccard similarity on normalised sets
    w_db = weights.get("databases", 0.0)
    if w_db > 0.0:
        a_db = set(_norm(d) for d in incoming.get("databases", []))
        b_db = set(_norm(d) for d in cached.get("databases", []))
        if not a_db and not b_db:
            total_score += w_db
        elif a_db and b_db:
            total_score += w_db * len(a_db & b_db) / len(a_db | b_db)

    # Date range — exact match pair
    w_dr = weights.get("date_range", 0.0)
    if w_dr > 0.0:
        if (_norm(incoming.get("date_range_start", "")) == _norm(cached.get("date_range_start", ""))
                and _norm(incoming.get("date_range_end", "")) == _norm(cached.get("date_range_end", ""))):
            total_score += w_dr

    # RoB tool — exact match
    w_rob = weights.get("rob_tool", 0.0)
    if w_rob > 0.0:
        if _norm(str(incoming.get("rob_tool", ""))) == _norm(str(cached.get("rob_tool", ""))):
            total_score += w_rob

    return min(total_score, 1.0)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())
