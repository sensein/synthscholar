"""Admin utilities for inspecting, listing, and clearing the review cache."""

from __future__ import annotations

import json
from datetime import timezone

from .models import CacheEntry
from .store import CacheStore


async def list_entries(store: CacheStore, include_expired: bool = False) -> list[dict]:
    """Return a summary list of all cache entries."""
    entries = await store.get_all_entries(include_expired=include_expired)
    return [_summarise(e) for e in entries]


async def inspect_entry(store: CacheStore, fingerprint: str) -> dict | None:
    """Return full detail for a single cache entry by fingerprint prefix or full hash."""
    entries = await store.get_all_entries(include_expired=True)
    match = next(
        (e for e in entries if e.criteria_fingerprint.startswith(fingerprint)), None
    )
    if not match:
        return None
    summary = _summarise(match)
    summary["criteria_json"] = match.criteria_json
    return summary


async def clear_all(store: CacheStore) -> int:
    """Delete all cache entries. Returns count deleted."""
    return await store.clear_all()


async def delete_entry(store: CacheStore, fingerprint: str) -> bool:
    """Delete a single entry by fingerprint prefix or full hash."""
    entries = await store.get_all_entries(include_expired=True)
    match = next(
        (e for e in entries if e.criteria_fingerprint.startswith(fingerprint)), None
    )
    if not match:
        return False
    return await store.delete_entry(match.criteria_fingerprint)


def _summarise(entry: CacheEntry) -> dict:
    now_aware = __import__("datetime").datetime.now(tz=timezone.utc)
    exp = entry.expires_at
    if exp and exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    expired = exp is not None and now_aware > exp
    return {
        "fingerprint": entry.criteria_fingerprint[:16] + "…",
        "full_fingerprint": entry.criteria_fingerprint,
        "model": entry.model_name,
        "title": entry.criteria_json.get("title", "")[:80],
        "created_at": entry.created_at.isoformat(),
        "expires_at": entry.expires_at.isoformat() if entry.expires_at else "never",
        "expired": expired,
    }
