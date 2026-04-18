"""Pydantic models for the review cache and article store."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator


class CacheUnavailableError(RuntimeError):
    """Raised when the PostgreSQL cache store cannot be reached."""


class CacheSchemaError(RuntimeError):
    """Raised when required tables are missing; run the migration first."""


class SimilarityConfig(BaseModel):
    """Configuration for the criteria similarity engine."""

    threshold: float = Field(default=0.95, ge=0.0, le=1.0,
                             description="Minimum score (0–1) to treat as a cache hit")
    field_weights: dict[str, float] = Field(default_factory=lambda: {
        "title": 0.25,
        "objective": 0.15,
        "inclusion_criteria": 0.20,
        "exclusion_criteria": 0.20,
        "pico_population": 0.05,
        "pico_intervention": 0.05,
        "pico_comparison": 0.025,
        "pico_outcome": 0.025,
        "databases": 0.03,
        "date_range": 0.01,
        "rob_tool": 0.01,
    })
    ttl_days: int = Field(default=30, ge=0,
                          description="Cache entry TTL in days; 0 = never expire")

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "SimilarityConfig":
        total = sum(self.field_weights.values())
        if not (0.999 <= total <= 1.001):
            raise ValueError(f"field_weights must sum to 1.0, got {total:.4f}")
        return self


class CacheEntry(BaseModel):
    """A single persisted review result in the cache."""

    id: int = 0
    criteria_fingerprint: str
    criteria_json: dict[str, Any]
    model_name: str
    result_json: dict[str, Any]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    similarity_score: float | None = None  # set when returned as a fuzzy match


class CacheLookupResult(BaseModel):
    """Result returned by the CacheAgent lookup tool."""

    hit: bool = False
    entry: CacheEntry | None = None
    similarity_score: float | None = None  # 0.0–1.0
    matched_fingerprint: str | None = None


class StoredArticle(BaseModel):
    """A fetched article persisted in the article_store table."""

    id: int = 0
    pmid: str
    title: str = ""
    abstract: str = ""
    authors: str = ""
    journal: str = ""
    year: str = ""
    doi: str = ""
    pmc_id: str = ""
    source: str = ""
    full_text: str = ""
    mesh_terms: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
