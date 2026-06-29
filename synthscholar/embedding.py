"""Embedding backend for semantic search across articles and review results.

The default model is sentence-transformers' ``all-MiniLM-L6-v2`` (384 dims,
~80 MB download, CPU-friendly). It is loaded lazily on first call so the
``import synthscholar.embedding`` cost stays near zero when semantic search
isn't used in a given process.

If the optional dependency isn't installed (``pip install
"synthscholar[semantic]"``), :func:`embed_text` and :func:`embed_batch`
return ``None`` and a one-time warning is logged. Callers should check the
return value and fall back to lexical search.

The dimension constant matches the SQL migration
:file:`synthscholar/cache/migrations/004_add_embeddings.sql` — change one,
change the other.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Optional, Sequence

logger = logging.getLogger(__name__)


# Must match the VECTOR(N) width in migration 004 and the model output dim.
EMBEDDING_DIM: int = 384

# Default backend. Override with SYNTHSCHOLAR_EMBED_MODEL env var if you've
# installed a different sentence-transformers model with the same dimension.
DEFAULT_MODEL_NAME: str = os.environ.get(
    "SYNTHSCHOLAR_EMBED_MODEL",
    "sentence-transformers/all-MiniLM-L6-v2",
)

_model_lock = threading.Lock()
_model = None  # type: ignore[assignment]
_warned_missing = False


def _load_model():
    """Lazy-load the sentence-transformer model (thread-safe)."""
    global _model, _warned_missing
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]
        except ImportError:
            if not _warned_missing:
                logger.warning(
                    "sentence-transformers not installed — semantic search disabled. "
                    "Install with: pip install 'synthscholar[semantic]'"
                )
                _warned_missing = True
            return None
        try:
            _model = SentenceTransformer(DEFAULT_MODEL_NAME)
            logger.info(
                "Loaded embedding model %s (dim=%d)",
                DEFAULT_MODEL_NAME, EMBEDDING_DIM,
            )
        except Exception as exc:
            logger.warning(
                "Failed to load embedding model %s: %s", DEFAULT_MODEL_NAME, exc,
            )
            return None
    return _model


def is_available() -> bool:
    """``True`` when the embedding backend is usable."""
    return _load_model() is not None


def embed_text(text: str) -> Optional[list[float]]:
    """Embed a single string. Returns ``None`` when the backend is unavailable
    or the input is empty / whitespace-only.
    """
    if not text or not text.strip():
        return None
    model = _load_model()
    if model is None:
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
        return vec.tolist()
    except Exception as exc:
        logger.info("embed_text failed: %s", exc)
        return None


def embed_batch(texts: Sequence[str]) -> Optional[list[Optional[list[float]]]]:
    """Embed a batch of strings. Returns ``None`` when the backend is
    unavailable; otherwise a list aligned with ``texts`` where each entry is
    a vector or ``None`` (for empty / whitespace inputs).

    Batching is significantly faster than calling :func:`embed_text` in a
    loop because the model amortises its forward pass.
    """
    model = _load_model()
    if model is None:
        return None
    if not texts:
        return []

    keep_indices: list[int] = []
    keep_texts: list[str] = []
    for i, t in enumerate(texts):
        if t and t.strip():
            keep_indices.append(i)
            keep_texts.append(t)

    if not keep_texts:
        return [None] * len(texts)

    try:
        vectors = model.encode(
            keep_texts,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=32,
        )
    except Exception as exc:
        logger.info("embed_batch failed: %s", exc)
        return None

    out: list[Optional[list[float]]] = [None] * len(texts)
    for src_idx, dst_idx in enumerate(keep_indices):
        out[dst_idx] = vectors[src_idx].tolist()
    return out


def article_text_for_embedding(article) -> str:
    """Compose the per-article text fed to the embedding model.

    Title + abstract carry most of the semantic signal; full text is
    truncated so the embedding stays representative rather than diluted by
    long methods/discussion sections.
    """
    title = (getattr(article, "title", "") or "").strip()
    abstract = (getattr(article, "abstract", "") or "").strip()
    full_text = (getattr(article, "full_text", "") or "").strip()[:4000]
    parts = [p for p in (title, abstract, full_text) if p]
    return " \n\n ".join(parts)


def protocol_text_for_embedding(protocol) -> str:
    """Compose the per-review text fed to the embedding model.

    Pulls the research question + PICO + criteria into one string so
    semantic search across past reviews can match by topic and intent.
    """
    p = protocol
    parts = [
        getattr(p, "title", ""),
        getattr(p, "objective", ""),
        getattr(p, "question", ""),
        getattr(p, "pico_population", ""),
        getattr(p, "pico_intervention", ""),
        getattr(p, "pico_comparison", ""),
        getattr(p, "pico_outcome", ""),
        getattr(p, "inclusion_criteria", ""),
        getattr(p, "exclusion_criteria", ""),
    ]
    return " \n ".join([str(s).strip() for s in parts if s])
