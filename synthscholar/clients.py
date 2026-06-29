"""
API clients for PubMed, bioRxiv/medRxiv, multi-source OA discovery, and full-text retrieval.

These are plain HTTP clients with no LLM dependency — they handle data
acquisition, OA full-text resolution, PDF parsing, and caching for the
PRISMA pipeline.

Search providers (parallel multi-source via :class:`OAFetcher`):
    PubMed, bioRxiv, medRxiv, Europe PMC, OpenAlex, CrossRef, DOAJ,
    Semantic Scholar, arXiv, CORE, Unpaywall.

Full-text resolution (DOI / PMCID → full text) via :class:`FullTextResolver`:
    Europe PMC (OA full text by ID) → Unpaywall → OpenAlex → Semantic Scholar
    → direct PDF download → marker-pdf parsing.
"""

from __future__ import annotations

import os
import re
import json
import time
import sqlite3
import hashlib
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import quote_plus

import httpx

from .models import Article


logger = logging.getLogger(__name__)


# ────────────────────────── Configuration ──────────────────────────────

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIORXIV_BASE = "https://api.biorxiv.org/details"
NCBI_TOOL = "synthscholar"
NCBI_EMAIL = "tekraj@mit.edu"
RATE_LIMIT_DELAY = 0.35

# Environment variable names that the API-key resolution pulls from when the
# caller does not supply an explicit api_keys dict. These are read once when
# `OAFetcher` / `FullTextResolver` are constructed; nothing is read mid-run.
ENV_API_KEYS = {
    "semantic_scholar": "SEMANTIC_SCHOLAR_API_KEY",
    "core": "CORE_API_KEY",
}

# Env var for the polite-pool contact email used in User-Agent and as the
# Unpaywall ``email=`` parameter. Resolution order: explicit argument >
# env var > NCBI_EMAIL default.
ENV_EMAIL = "SYNTHSCHOLAR_EMAIL"


def _resolve_api_keys(api_keys: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Merge an explicit ``api_keys`` dict with environment-variable fallbacks.

    Explicit values always win. Empty / unset env vars are ignored.
    """
    resolved: Dict[str, str] = {}
    for provider_name, env_var in ENV_API_KEYS.items():
        env_val = os.environ.get(env_var, "").strip()
        if env_val:
            resolved[provider_name] = env_val
    if api_keys:
        for k, v in api_keys.items():
            if v:
                resolved[k] = v
    return resolved


def _resolve_email(email: Optional[str]) -> str:
    """Resolve the polite-pool contact email.

    Order: explicit argument > ``SYNTHSCHOLAR_EMAIL`` env var > ``NCBI_EMAIL``
    module default.
    """
    if email and email.strip():
        return email.strip()
    env_val = os.environ.get(ENV_EMAIL, "").strip()
    if env_val:
        return env_val
    return NCBI_EMAIL


# ────────────────────────── SQLite Cache ───────────────────────────────

class Cache:
    """Persistent key-value cache backed by SQLite with TTL expiry."""

    def __init__(self, db_path: str | Path = "prisma_agent_cache.db",
                 ttl_hours: int = 72):
        self.ttl = timedelta(hours=ttl_hours)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS cache "
            "(key TEXT PRIMARY KEY, value TEXT, created_at TEXT)"
        )
        self.conn.commit()

    def _key(self, ns: str, ident: str) -> str:
        return hashlib.sha256(f"{ns}:{ident}".encode()).hexdigest()

    def get(self, ns: str, ident: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT value, created_at FROM cache WHERE key = ?",
            (self._key(ns, ident),),
        ).fetchone()
        if not row:
            return None
        if datetime.now() - datetime.fromisoformat(row[1]) > self.ttl:
            self.conn.execute(
                "DELETE FROM cache WHERE key = ?", (self._key(ns, ident),)
            )
            self.conn.commit()
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def set(self, ns: str, ident: str, value: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
            (self._key(ns, ident),
             json.dumps(value, ensure_ascii=False),
             datetime.now().isoformat()),
        )
        self.conn.commit()

    def clear(self):
        self.conn.execute("DELETE FROM cache")
        self.conn.commit()


# ────────────────────────── PubMed Client ──────────────────────────────

class PubMedClient:
    """NCBI E-utilities client for PubMed search, fetch, and link navigation."""

    def __init__(self, email: str = NCBI_EMAIL, api_key: str = "",
                 cache: Optional[Cache] = None):
        self.email = email
        self.api_key = api_key
        self.client = httpx.Client(timeout=30)
        self.cache = cache

    def _params(self, **kw) -> dict:
        p = {"tool": NCBI_TOOL, "email": self.email, "retmode": "json"}
        if self.api_key:
            p["api_key"] = self.api_key
        p.update(kw)
        return p

    def _get_json(self, ep: str, **kw) -> dict:
        time.sleep(RATE_LIMIT_DELAY)
        r = self.client.get(f"{NCBI_BASE}/{ep}", params=self._params(**kw))
        r.raise_for_status()
        return r.json()

    def _get_xml(self, ep: str, **kw) -> str:
        time.sleep(RATE_LIMIT_DELAY)
        p = {"tool": NCBI_TOOL, "email": self.email, "retmode": "xml"}
        if self.api_key:
            p["api_key"] = self.api_key
        p.update(kw)
        r = self.client.get(f"{NCBI_BASE}/{ep}", params=p)
        r.raise_for_status()
        return r.text

    # ── Search ──

    def search(self, query: str, max_results: int = 20,
               date_start: str = "", date_end: str = "") -> list[str]:
        cache_key = f"{query}_{max_results}_{date_start}_{date_end}"
        if self.cache:
            c = self.cache.get("search", cache_key)
            if c:
                return c["pmids"]

        params: dict = dict(
            db="pubmed", term=query, retmax=max_results, sort="relevance"
        )
        if date_start:
            params["mindate"] = date_start.replace("-", "/")
        if date_end:
            params["maxdate"] = date_end.replace("-", "/")
        if date_start or date_end:
            params["datetype"] = "pdat"

        d = self._get_json("esearch.fcgi", **params)
        pmids = d.get("esearchresult", {}).get("idlist", [])
        if self.cache:
            self.cache.set("search", cache_key, {"pmids": pmids})
        return pmids

    # ── Fetch articles ──

    def fetch_articles(self, pmids: list[str]) -> list[Article]:
        if not pmids:
            return []
        articles: list[Article] = []
        uncached: list[str] = []

        if self.cache:
            for p in pmids:
                c = self.cache.get("article", p)
                if c:
                    articles.append(Article(**c))
                else:
                    uncached.append(p)
        else:
            uncached = list(pmids)

        for i in range(0, len(uncached), 50):
            batch = uncached[i:i + 50]
            xml = self._get_xml(
                "efetch.fcgi", db="pubmed", id=",".join(batch), rettype="xml"
            )
            fetched = self._parse_xml(xml)
            if self.cache:
                for a in fetched:
                    self.cache.set("article", a.pmid, a.model_dump())
            articles.extend(fetched)
        return articles

    def _parse_xml(self, xml: str) -> list[Article]:
        articles = []
        for block in re.split(r"<PubmedArticle>", xml)[1:]:
            data: dict = {"pmid": ""}

            m = re.search(r"<PMID[^>]*>(\d+)</PMID>", block)
            if m:
                data["pmid"] = m.group(1)
            m = re.search(r"<ArticleTitle>(.*?)</ArticleTitle>", block, re.DOTALL)
            if m:
                data["title"] = re.sub(r"<[^>]+>", "", m.group(1)).strip()

            data["abstract"] = " ".join(
                re.sub(r"<[^>]+>", "", p).strip()
                for p in re.findall(
                    r"<AbstractText[^>]*>(.*?)</AbstractText>", block, re.DOTALL
                )
            )

            au = re.findall(
                r"<LastName>(.*?)</LastName>\s*<ForeName>(.*?)</ForeName>", block
            )
            if au:
                names = [f"{l} {f}" for l, f in au[:6]]
                if len(au) > 6:
                    names.append("et al.")
                data["authors"] = ", ".join(names)

            m = re.search(r"<Title>(.*?)</Title>", block)
            if m:
                data["journal"] = m.group(1).strip()

            m = re.search(r"<PubDate>\s*<Year>(\d{4})</Year>", block)
            if m:
                data["year"] = m.group(1)
            else:
                m = re.search(r"<MedlineDate>(\d{4})", block)
                if m:
                    data["year"] = m.group(1)

            m = re.search(r'<ArticleId IdType="doi">(.*?)</ArticleId>', block)
            if m:
                data["doi"] = m.group(1).strip()
            m = re.search(r'<ArticleId IdType="pmc">(PMC\d+)</ArticleId>', block)
            if m:
                data["pmc_id"] = m.group(1)

            data["mesh_terms"] = list(dict.fromkeys(
                re.findall(r"<DescriptorName[^>]*>(.*?)</DescriptorName>", block)
            ))
            data["keywords"] = list(dict.fromkeys(
                re.findall(r"<Keyword[^>]*>(.*?)</Keyword>", block)
            ))

            if data["pmid"]:
                articles.append(Article(**data))
        return articles

    # ── Related articles (elink neighbor_score) ──

    def find_related(self, pmids: list[str], max_results: int = 10) -> list[str]:
        if not pmids:
            return []
        ck = ",".join(sorted(pmids))
        if self.cache:
            c = self.cache.get("related", ck)
            if c:
                return c["pmids"]

        d = self._get_json(
            "elink.fcgi", dbfrom="pubmed", db="pubmed",
            id=",".join(pmids), cmd="neighbor_score",
        )
        related: list[str] = []
        for ls in d.get("linksets", []):
            for ldb in ls.get("linksetdbs", []):
                if ldb.get("linkname") == "pubmed_pubmed":
                    for lk in ldb.get("links", [])[:max_results]:
                        pid = str(lk.get("id", lk)) if isinstance(lk, dict) else str(lk)
                        if pid not in pmids and pid not in related:
                            related.append(pid)
        result = related[:max_results]
        if self.cache:
            self.cache.set("related", ck, {"pmids": result})
        return result

    # ── Forward citations (cited-by) ──

    def find_cited_by(self, pmids: list[str], max_results: int = 10) -> list[str]:
        if not pmids:
            return []
        try:
            d = self._get_json(
                "elink.fcgi", dbfrom="pubmed", db="pubmed",
                id=",".join(pmids), linkname="pubmed_pubmed_citedin",
            )
            cited: list[str] = []
            for ls in d.get("linksets", []):
                for ldb in ls.get("linksetdbs", []):
                    for lk in ldb.get("links", [])[:max_results]:
                        pid = str(lk.get("id", lk)) if isinstance(lk, dict) else str(lk)
                        if pid not in pmids and pid not in cited:
                            cited.append(pid)
            return cited[:max_results]
        except Exception:
            return []

    # ── Full-text retrieval (PMC) ──

    def fetch_full_text(self, pmc_ids: list[str]) -> dict[str, str]:
        results: dict[str, str] = {}
        for pid in pmc_ids[:10]:
            if self.cache:
                c = self.cache.get("fulltext", pid)
                if c:
                    results[pid] = c["text"]
                    continue
            try:
                xml = self._get_xml(
                    "efetch.fcgi", db="pmc", id=pid.replace("PMC", "")
                )
                body = re.findall(r"<body>(.*?)</body>", xml, re.DOTALL)
                if body:
                    text = re.sub(r"<[^>]+>", " ", body[0])
                    text = re.sub(r"\s+", " ", text).strip()[:12000]
                    results[pid] = text
                    if self.cache:
                        self.cache.set("fulltext", pid, {"text": text})
            except Exception:
                continue
        return results


# ────────────────────────── bioRxiv / medRxiv Client ──────────────────

class BioRxivClient:
    """bioRxiv / medRxiv preprint search via the public API.

    Both servers share the same API surface; `server` selects which one.
    """

    SUPPORTED_SERVERS = ("biorxiv", "medrxiv")

    def __init__(self, cache: Optional[Cache] = None,
                 server: str = "biorxiv"):
        if server not in self.SUPPORTED_SERVERS:
            raise ValueError(
                f"Unsupported server '{server}'. "
                f"Use one of: {self.SUPPORTED_SERVERS}"
            )
        self.server = server
        self.client = httpx.Client(timeout=15)
        self.cache = cache

    @property
    def journal_label(self) -> str:
        return "bioRxiv (Preprint)" if self.server == "biorxiv" else "medRxiv (Preprint)"

    @property
    def pmid_prefix(self) -> str:
        return f"{self.server}_"

    def search(self, query: str, max_results: int = 10,
               days_back: int = 180) -> list[Article]:
        cache_key = f"{self.server}_{query}_{days_back}"
        if self.cache:
            c = self.cache.get("preprint", cache_key)
            if c:
                return [Article(**a) for a in c["articles"]]
        try:
            today = datetime.now()
            start = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
            articles: list[Article] = []
            qw = set(query.lower().split())
            base_url = f"{BIORXIV_BASE}/{self.server}"

            for cursor in range(0, 150, 30):
                r = self.client.get(f"{base_url}/{start}/{end}/{cursor}/30")
                if r.status_code != 200:
                    break
                for item in r.json().get("collection", []):
                    combined = (
                        item.get("title", "") + " " + item.get("abstract", "")
                    ).lower()
                    score = sum(1 for w in qw if len(w) > 3 and w in combined)
                    if score >= 2:
                        doi = item.get("doi", "")
                        articles.append(Article(
                            pmid=f"{self.pmid_prefix}{doi.split('/')[-1]}",
                            title=item.get("title", ""),
                            abstract=item.get("abstract", ""),
                            authors=item.get("authors", ""),
                            journal=self.journal_label,
                            year=item.get("date", "")[:4],
                            doi=doi,
                            source=self.server,
                        ))
                if len(articles) >= max_results:
                    break

            result = articles[:max_results]
            if self.cache:
                self.cache.set(
                    "preprint", cache_key,
                    {"articles": [a.model_dump() for a in result]},
                )
            return result
        except Exception:
            return []

    def latest_version(self, doi: str) -> int:
        """Return the latest version number for a preprint via the bioRxiv API.

        Calls ``api.biorxiv.org/details/{server}/{DOI}/na/json`` and picks the
        max ``version`` across the returned records. Returns 1 on any failure
        (network error, missing record, malformed JSON) so callers can still
        attempt a v1 download.
        """
        if not doi:
            return 1
        # bioRxiv's details endpoint expects the DOI as a raw path segment
        # with its native `/` preserved — URL-encoding the slash returns 404.
        try:
            r = self.client.get(
                f"{BIORXIV_BASE}/{self.server}/{doi.strip()}/na/json"
            )
            r.raise_for_status()
            records = r.json().get("collection", [])
        except Exception as e:
            logger.info("[%s] latest_version lookup failed for %s: %s", self.server, doi, e)
            return 1
        if not records:
            return 1
        versions = [int(rec.get("version", 1)) for rec in records if rec.get("version")]
        return max(versions) if versions else 1

    def pdf_url(self, doi: str, version: Optional[int] = None) -> Optional[str]:
        """Canonical public PDF URL for a bioRxiv/medRxiv preprint.

        Uses the ``{doi}v{N}.full.pdf`` form bioRxiv documents — when no
        ``version`` is supplied, the latest version is fetched from the
        details API. Note that bioRxiv/medRxiv front Cloudflare on
        ``www.biorxiv.org`` and may serve a challenge to non-browser clients.
        Callers should validate ``Content-Type`` and the ``%PDF-`` magic
        bytes before treating the response as a PDF; see
        :meth:`FullTextResolver._download_and_parse`.
        """
        if not doi:
            return None
        host = "www.biorxiv.org" if self.server == "biorxiv" else "www.medrxiv.org"
        if version is None:
            version = self.latest_version(doi)
        return f"https://{host}/content/{doi.strip()}v{version}.full.pdf"


class MedRxivClient(BioRxivClient):
    """medRxiv preprint search — sister server to bioRxiv, identical API."""

    def __init__(self, cache: Optional[Cache] = None):
        super().__init__(cache=cache, server="medrxiv")


# ────────────────────────── OA Publication Model ────────────────────────

@dataclass
class Publication:
    """Normalized publication record across all OA providers."""
    source: str
    title: str
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    abstract: Optional[str] = None
    url: Optional[str] = None
    pdf_url: Optional[str] = None
    open_access: bool = False
    venue: Optional[str] = None
    citations: Optional[int] = None
    external_ids: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_article(self) -> Article:
        """Convert Publication to Article model for pipeline."""
        return Article(
            pmid=(
                self.external_ids.get("PMID")
                or self.external_ids.get("arXiv")
                or self.external_ids.get("DOI")
                or f"{self.source}_{self.title[:30]}"
            ),
            title=self.title,
            abstract=self.abstract or "",
            authors=", ".join(self.authors[:6]) + (" et al." if len(self.authors) > 6 else ""),
            journal=self.venue or self.source,
            year=str(self.year) if self.year else "",
            doi=self.doi,
            source=self.source,
        )


# ────────────────────────── OA Base Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BaseOAProvider(ABC):
    """Abstract base class for OA publication providers."""
    name: str = "base"
    rate_limit_sec: float = 1.0
    timeout: int = 30

    def __init__(self, email: Optional[str] = None, api_key: Optional[str] = None):
        self.email = email
        self.api_key = api_key
        self._session = httpx.Client(timeout=self.timeout)
        self._session.headers.update({
            "User-Agent": f"SynthScholar/1.0 (mailto:{email or NCBI_EMAIL})"
        })

    def _get(self, url: str, params: Optional[dict] = None,
             headers: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            time.sleep(self.rate_limit_sec)
            return r.json()
        except httpx.HTTPStatusError as e:
            logger.info("[%s] HTTP %s on %s", self.name, e.response.status_code, url)
        except httpx.RequestError as e:
            logger.info("[%s] request failed: %s", self.name, e)
        except ValueError:
            logger.info("[%s] non-JSON response from %s", self.name, url)
        return None

    def _get_raw(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        try:
            r = self._session.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            time.sleep(self.rate_limit_sec)
            return r.text
        except httpx.RequestError as e:
            logger.info("[%s] raw request failed: %s", self.name, e)
            return None

    @abstractmethod
    def search(self, query: str, limit: int = 25) -> List[Publication]:
        ...

    def get_by_doi(self, doi: str) -> Optional[Publication]:
        """Resolve a single publication by DOI. Default: not supported."""
        return None


# ────────────────────────── OpenAlex Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━

class OpenAlexProvider(BaseOAProvider):
    """OpenAlex — comprehensive free scholarly graph."""
    name = "openalex"
    BASE = "https://api.openalex.org/works"
    rate_limit_sec = 0.1

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        params = {"search": query, "per-page": min(limit, 200)}
        if self.email:
            params["mailto"] = self.email
        data = self._get(self.BASE, params=params)
        if not data:
            return []
        return [self._to_publication(w) for w in data.get("results", [])]

    def get_by_doi(self, doi: str) -> Optional[Publication]:
        if not doi:
            return None
        params = {"mailto": self.email} if self.email else None
        data = self._get(f"{self.BASE}/doi:{doi}", params=params)
        return self._to_publication(data) if data else None

    def _to_publication(self, w: dict) -> Publication:
        oa = w.get("open_access") or {}
        pdf_url = oa.get("oa_url")
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in w.get("authorships", [])
        ]
        venue = ((w.get("primary_location") or {}).get("source") or {}).get("display_name")

        doi = w.get("doi")
        if doi and doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")

        return Publication(
            source=self.name,
            title=w.get("title") or "",
            authors=[a for a in authors if a],
            year=w.get("publication_year"),
            doi=doi,
            abstract=self._reconstruct_abstract(w.get("abstract_inverted_index")),
            url=w.get("id"),
            pdf_url=pdf_url,
            open_access=bool(oa.get("is_oa")),
            venue=venue,
            citations=w.get("cited_by_count"),
            external_ids={
                "OpenAlex": w.get("id", ""),
                "DOI": doi or "",
            },
        )

    @staticmethod
    def _reconstruct_abstract(inverted: Optional[dict]) -> Optional[str]:
        if not inverted:
            return None
        positions: Dict[int, str] = {}
        for word, idxs in inverted.items():
            for i in idxs:
                positions[i] = word
        return " ".join(positions[i] for i in sorted(positions))


# ────────────────────────── Europe PMC Provider ━━━━━━━━━━━━━━━━━━━━━━━

class EuropePMCProvider(BaseOAProvider):
    """Europe PMC — life-sciences OA full text and preprint coverage."""
    name = "europe_pmc"
    BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"
    rate_limit_sec = 0.5

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        params = {
            "query": query, "format": "json",
            "pageSize": min(limit, 100), "resultType": "core",
        }
        data = self._get(f"{self.BASE}/search", params=params)
        if not data:
            return []
        return [
            self._to_publication(item)
            for item in (data.get("resultList") or {}).get("result", [])
        ]

    def get_by_doi(self, doi: str) -> Optional[Publication]:
        if not doi:
            return None
        results = self.search(f'DOI:"{doi}"', limit=1)
        return results[0] if results else None

    def fetch_full_text_xml(self, pmcid: str) -> Optional[str]:
        """Retrieve OA full-text XML for a PMC ID via Europe PMC."""
        if not pmcid:
            return None
        pmcid_clean = pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"
        return self._get_raw(f"{self.BASE}/{pmcid_clean}/fullTextXML")

    def _to_publication(self, item: dict) -> Publication:
        pmcid = item.get("pmcid")
        doi = item.get("doi")
        is_oa = item.get("isOpenAccess") == "Y" or item.get("inPMC") == "Y"
        pdf_url = (
            f"https://europepmc.org/articles/{pmcid}?pdf=render" if pmcid else None
        )

        try:
            year = int(item.get("pubYear")) if item.get("pubYear") else None
        except ValueError:
            year = None

        return Publication(
            source=self.name,
            title=item.get("title", "").rstrip("."),
            authors=[a.strip() for a in (item.get("authorString") or "").split(",") if a.strip()],
            year=year,
            doi=doi,
            abstract=item.get("abstractText"),
            url=f"https://europepmc.org/article/{item.get('source')}/{item.get('id')}",
            pdf_url=pdf_url,
            open_access=is_oa,
            venue=item.get("journalTitle"),
            citations=item.get("citedByCount"),
            external_ids={k: str(v) for k, v in {
                "PMID": item.get("pmid"), "PMCID": pmcid, "source": item.get("source"),
            }.items() if v},
        )


# ────────────────────────── CrossRef Provider ━━━━━━━━━━━━━━━━━━━━━━━━━

class CrossRefProvider(BaseOAProvider):
    """CrossRef — DOI-centric metadata."""
    name = "crossref"
    BASE = "https://api.crossref.org/works"
    rate_limit_sec = 0.1

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        params = {"query": query, "rows": min(limit, 100)}
        if self.email:
            params["mailto"] = self.email
        data = self._get(self.BASE, params=params)
        if not data:
            return []
        return [
            self._to_publication(item)
            for item in (data.get("message") or {}).get("items", [])
        ]

    def get_by_doi(self, doi: str) -> Optional[Publication]:
        if not doi:
            return None
        params = {"mailto": self.email} if self.email else None
        data = self._get(f"{self.BASE}/{quote_plus(doi)}", params=params)
        if not data:
            return None
        msg = data.get("message")
        return self._to_publication(msg) if msg else None

    def _to_publication(self, item: dict) -> Publication:
        licenses = item.get("license", [])
        is_oa = any("creativecommons" in (l.get("URL") or "").lower() for l in licenses)

        year = None
        for dkey in ("published-print", "published-online", "issued", "created"):
            parts = (item.get(dkey) or {}).get("date-parts")
            if parts and parts[0]:
                year = parts[0][0]
                break

        authors = []
        for a in item.get("author", []):
            name = " ".join(p for p in [a.get("given"), a.get("family")] if p)
            if name:
                authors.append(name)

        title = (item.get("title") or [""])[0]
        venue = (item.get("container-title") or [""])[0]

        return Publication(
            source=self.name,
            title=title,
            authors=authors,
            year=year,
            doi=item.get("DOI"),
            abstract=item.get("abstract"),
            url=item.get("URL"),
            open_access=is_oa,
            venue=venue,
            citations=item.get("is-referenced-by-count"),
            external_ids={"DOI": item.get("DOI", "")},
        )


# ────────────────────────── DOAJ Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DOAJProvider(BaseOAProvider):
    """Directory of Open Access Journals — article search."""
    name = "doaj"
    BASE = "https://doaj.org/api/search/articles"
    rate_limit_sec = 1.0

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        url = f"{self.BASE}/{quote_plus(query)}"
        data = self._get(url, params={"pageSize": min(limit, 100)})
        if not data:
            return []

        results = []
        for item in data.get("results", []):
            b = item.get("bibjson", {})
            doi = next(
                (i.get("id") for i in b.get("identifier", []) if i.get("type") == "doi"),
                None,
            )
            fulltext = next(
                (l.get("url") for l in b.get("link", []) if l.get("type") == "fulltext"),
                None,
            )
            try:
                year = int(b.get("year")) if b.get("year") else None
            except ValueError:
                year = None

            results.append(Publication(
                source=self.name,
                title=b.get("title", ""),
                authors=[a.get("name", "") for a in b.get("author", [])],
                year=year,
                doi=doi,
                abstract=b.get("abstract"),
                url=fulltext,
                pdf_url=fulltext,
                open_access=True,
                venue=(b.get("journal") or {}).get("title"),
                external_ids={"DOAJ": item.get("id", ""), "DOI": doi or ""},
            ))
        return results


# ────────────────────────── Semantic Scholar Provider ━━━━━━━━━━━━━━━━━━

class SemanticScholarProvider(BaseOAProvider):
    """Semantic Scholar Graph API."""
    name = "semantic_scholar"
    BASE = "https://api.semanticscholar.org/graph/v1"
    rate_limit_sec = 1.0
    FIELDS = ",".join([
        "title", "authors", "year", "abstract", "externalIds",
        "openAccessPdf", "venue", "citationCount", "isOpenAccess", "url",
    ])

    def _headers(self) -> dict:
        return {"x-api-key": self.api_key} if self.api_key else {}

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        params = {"query": query, "limit": min(limit, 100), "fields": self.FIELDS}
        data = self._get(
            f"{self.BASE}/paper/search", params=params, headers=self._headers()
        )
        if not data:
            return []
        return [self._to_publication(p) for p in data.get("data", [])]

    def get_by_doi(self, doi: str) -> Optional[Publication]:
        if not doi:
            return None
        data = self._get(
            f"{self.BASE}/paper/DOI:{doi}",
            params={"fields": self.FIELDS},
            headers=self._headers(),
        )
        return self._to_publication(data) if data else None

    def _to_publication(self, p: dict) -> Publication:
        ext = p.get("externalIds") or {}
        pdf = (p.get("openAccessPdf") or {}).get("url")
        return Publication(
            source=self.name,
            title=p.get("title") or "",
            authors=[a.get("name", "") for a in (p.get("authors") or [])],
            year=p.get("year"),
            doi=ext.get("DOI"),
            abstract=p.get("abstract"),
            url=p.get("url"),
            pdf_url=pdf,
            open_access=bool(p.get("isOpenAccess") or pdf),
            venue=p.get("venue"),
            citations=p.get("citationCount"),
            external_ids={k: str(v) for k, v in ext.items()},
        )


# ────────────────────────── arXiv Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ArxivProvider(BaseOAProvider):
    """arXiv API (Atom XML via feedparser)."""
    name = "arxiv"
    BASE = "http://export.arxiv.org/api/query"
    rate_limit_sec = 3.0

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        try:
            import feedparser  # type: ignore[import-untyped]
        except ImportError:
            logger.warning(
                "feedparser not installed — arXiv provider disabled. "
                "Install with: pip install feedparser"
            )
            return []
        params = {
            "search_query": f"all:{query}",
            "start": 0, "max_results": min(limit, 100),
            "sortBy": "relevance", "sortOrder": "descending",
        }
        raw = self._get_raw(self.BASE, params=params)
        if not raw:
            return []
        feed = feedparser.parse(raw)
        results = []
        for entry in feed.entries:
            arxiv_id = entry.id.rsplit("/", 1)[-1]
            pdf_url = next(
                (l.href for l in entry.get("links", []) if l.get("type") == "application/pdf"),
                f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            )
            year = None
            if entry.get("published"):
                try:
                    year = int(entry.published[:4])
                except ValueError:
                    pass
            results.append(Publication(
                source=self.name,
                title=entry.title.replace("\n", " ").strip(),
                authors=[a.name for a in entry.get("authors", [])],
                year=year,
                doi=entry.get("arxiv_doi"),
                abstract=entry.get("summary", "").replace("\n", " ").strip(),
                url=entry.link,
                pdf_url=pdf_url,
                open_access=True,
                venue="arXiv",
                external_ids={"arXiv": arxiv_id},
            ))
        return results


# ────────────────────────── CORE Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CoreProvider(BaseOAProvider):
    """CORE API v3 — worldwide OA aggregator (requires free API key)."""
    name = "core"
    BASE = "https://api.core.ac.uk/v3"
    rate_limit_sec = 1.0

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        if not self.api_key:
            return []
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {"q": query, "limit": min(limit, 100)}
        try:
            r = self._session.post(
                f"{self.BASE}/search/works",
                json=payload, headers=headers, timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json()
            time.sleep(self.rate_limit_sec)
        except httpx.RequestError as e:
            logger.info("[core] request failed: %s", e)
            return []
        results = []
        for item in data.get("results", []):
            results.append(Publication(
                source=self.name,
                title=item.get("title", ""),
                authors=[a.get("name", "") for a in item.get("authors", [])],
                year=item.get("yearPublished"),
                doi=item.get("doi"),
                abstract=item.get("abstract"),
                url=(item.get("sourceFulltextUrls") or [None])[0] or item.get("downloadUrl"),
                pdf_url=item.get("downloadUrl"),
                open_access=True,
                venue=item.get("publisher") or None,
                external_ids={"CORE": str(item.get("id", "")), "DOI": item.get("doi") or ""},
            ))
        return results


# ────────────────────────── Unpaywall Provider ━━━━━━━━━━━━━━━━━━━━━━━━━

class UnpaywallProvider(BaseOAProvider):
    """Unpaywall — DOI → OA URL resolver (email required by ToS)."""
    name = "unpaywall"
    BASE = "https://api.unpaywall.org/v2"
    rate_limit_sec = 0.1

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        if not self.email:
            return []
        params = {"query": query, "email": self.email}
        data = self._get(f"{self.BASE}/search", params=params)
        if not data:
            return []
        return [
            self._to_publication(item.get("response") or {})
            for item in (data.get("results") or [])[:limit]
        ]

    def get_by_doi(self, doi: str) -> Optional[Publication]:
        if not doi or not self.email:
            return None
        data = self._get(f"{self.BASE}/{quote_plus(doi)}", params={"email": self.email})
        return self._to_publication(data) if data else None

    def _to_publication(self, r: dict) -> Publication:
        best_oa = r.get("best_oa_location") or {}
        return Publication(
            source=self.name,
            title=r.get("title") or "",
            authors=[
                f"{a.get('given', '')} {a.get('family', '')}".strip()
                for a in (r.get("z_authors") or [])
            ],
            year=r.get("year"),
            doi=r.get("doi"),
            url=r.get("doi_url"),
            pdf_url=best_oa.get("url_for_pdf") or best_oa.get("url"),
            open_access=bool(r.get("is_oa")),
            venue=r.get("journal_name"),
            external_ids={"DOI": r.get("doi", "")},
        )


# ────────────────────────── PubMed OA Provider ━━━━━━━━━━━━━━━━━━━━━━━━━

class PubMedOAProvider(BaseOAProvider):
    """PubMed via NCBI E-utilities, returning Publication records.

    Wraps the lower-level :class:`PubMedClient` shape into the OA provider
    interface so PubMed can participate in :class:`OAFetcher` parallel fan-out.
    """
    name = "pubmed"
    BASE = NCBI_BASE
    rate_limit_sec = 0.34

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        params: dict = {
            "db": "pubmed", "term": query, "retmax": limit,
            "retmode": "json", "tool": NCBI_TOOL,
        }
        if self.email:
            params["email"] = self.email
        if self.api_key:
            params["api_key"] = self.api_key
        data = self._get(f"{self.BASE}/esearch.fcgi", params=params)
        if not data:
            return []
        pmids = (data.get("esearchresult") or {}).get("idlist") or []
        if not pmids:
            return []

        summary_params: dict = {
            "db": "pubmed", "id": ",".join(pmids),
            "retmode": "json", "tool": NCBI_TOOL,
        }
        if self.email:
            summary_params["email"] = self.email
        if self.api_key:
            summary_params["api_key"] = self.api_key
        summary = self._get(f"{self.BASE}/esummary.fcgi", params=summary_params)
        if not summary:
            return []

        results = []
        sresult = summary.get("result", {})
        for pmid in pmids:
            item = sresult.get(pmid)
            if not item:
                continue
            doi = pmcid = None
            for aid in item.get("articleids", []):
                if aid.get("idtype") == "doi":
                    doi = aid.get("value")
                if aid.get("idtype") == "pmc":
                    pmcid = aid.get("value")
            pdf_url = (
                f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/"
                if pmcid else None
            )
            year = None
            pub_date = item.get("pubdate", "")
            if pub_date:
                try:
                    year = int(pub_date.split(" ")[0])
                except (ValueError, IndexError):
                    pass
            results.append(Publication(
                source=self.name,
                title=item.get("title", "").rstrip("."),
                authors=[a.get("name", "") for a in item.get("authors", [])],
                year=year,
                doi=doi,
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                pdf_url=pdf_url,
                open_access=bool(pmcid),
                venue=item.get("fulljournalname") or item.get("source"),
                external_ids={"PMID": pmid, **({"PMCID": pmcid} if pmcid else {})},
            ))
        return results


# ────────────────────────── OA Aggregator ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class OAFetcher:
    """Query multiple OA providers in parallel and return unified results."""

    def __init__(
        self,
        email: Optional[str] = None,
        api_keys: Optional[Dict[str, str]] = None,
        cache: Optional[Cache] = None,
    ):
        # Pulls SEMANTIC_SCHOLAR_API_KEY / CORE_API_KEY from env unless an
        # explicit dict is provided (explicit values win). Email falls back to
        # SYNTHSCHOLAR_EMAIL env var when not passed explicitly.
        api_keys = _resolve_api_keys(api_keys)
        email = _resolve_email(email)
        self.cache = cache
        self.providers: Dict[str, BaseOAProvider] = {
            "semantic_scholar": SemanticScholarProvider(email=email, api_key=api_keys.get("semantic_scholar")),
            "pubmed":           PubMedOAProvider(email=email, api_key=api_keys.get("pubmed")),
            "europe_pmc":       EuropePMCProvider(email=email),
            "arxiv":            ArxivProvider(email=email),
            "crossref":         CrossRefProvider(email=email),
            "openalex":         OpenAlexProvider(email=email),
            "doaj":             DOAJProvider(email=email),
            "core":             CoreProvider(email=email, api_key=api_keys.get("core")),
            "unpaywall":        UnpaywallProvider(email=email),
        }

    def search_all(
        self,
        query: str,
        limit_per_source: int = 10,
        sources: Optional[List[str]] = None,
        only_open_access: bool = True,
        max_workers: int = 6,
    ) -> List[Publication]:
        """Query selected providers in parallel and merge results, deduping by DOI."""
        selected = [s for s in (sources or list(self.providers.keys())) if s in self.providers]
        unknown = [s for s in (sources or []) if s not in self.providers]
        for name in unknown:
            logger.warning("Unknown OA source: %s", name)

        all_results: List[Publication] = []

        def _search_one(name: str) -> List[Publication]:
            try:
                return self.providers[name].search(query, limit=limit_per_source)
            except Exception as e:
                logger.info("[%s] search failed: %s", name, e)
                return []

        with ThreadPoolExecutor(max_workers=min(max_workers, len(selected))) as executor:
            futures = {executor.submit(_search_one, name): name for name in selected}
            for future in as_completed(futures):
                all_results.extend(future.result())

        if only_open_access:
            all_results = [p for p in all_results if p.open_access]

        return self._dedupe(all_results)

    @staticmethod
    def _dedupe(pubs: List[Publication]) -> List[Publication]:
        """Deduplicate by DOI (fallback: title)."""
        seen: Dict[str, Publication] = {}
        for p in pubs:
            key = (p.doi or "").lower().strip() or p.title.lower().strip()
            if not key:
                continue
            if key not in seen:
                seen[key] = p
            else:
                existing = seen[key]
                if not existing.pdf_url and p.pdf_url:
                    existing.pdf_url = p.pdf_url
                if not existing.abstract and p.abstract:
                    existing.abstract = p.abstract
                existing.external_ids.update(p.external_ids)
        return list(seen.values())

    def search_as_articles(
        self,
        query: str,
        limit_per_source: int = 10,
        sources: Optional[List[str]] = None,
    ) -> List[Article]:
        """Search OA sources and return as Article objects for pipeline."""
        publications = self.search_all(query, limit_per_source, sources, only_open_access=True)
        return [p.to_article() for p in publications]


# ────────────────────────── PDF Parsing (PyMuPDF) ━━━━━━━━━━━━━━━━━━━━

class PyMuPdfParser:
    """Parse PDFs to plain text using PyMuPDF (``pymupdf`` / ``fitz``).

    PyMuPDF is an optional dependency installed via the ``[fulltext]`` extra.
    If unavailable, parsing methods return None and a one-time warning is
    emitted. Install with::

        pip install "synthscholar[fulltext]"

    or directly: ``pip install pymupdf``.

    PyMuPDF was chosen over ``marker-pdf`` because the latter pins
    ``anthropic<0.47``, which conflicts with this project's ``pydantic-ai``
    dependency. PyMuPDF has no such conflict and is dramatically lighter
    (no torch / no ML models). The trade-off is that table structure and
    equations are flattened to plain text — acceptable since the parsed
    text is consumed by an LLM rather than rendered.
    """

    _warned_missing = False

    def __init__(self, max_chars: int = 30000):
        self.max_chars = max_chars
        self._fitz = self._load_lazy()

    def _load_lazy(self):
        try:
            import pymupdf as fitz  # type: ignore[import-untyped]
            return fitz
        except ImportError:
            try:
                import fitz  # type: ignore[import-untyped]
                return fitz
            except ImportError:
                if not PyMuPdfParser._warned_missing:
                    logger.warning(
                        "pymupdf not installed — PDF parsing disabled. "
                        "Install with: pip install pymupdf"
                    )
                    PyMuPdfParser._warned_missing = True
                return None

    @property
    def available(self) -> bool:
        return self._fitz is not None

    def parse_path(self, pdf_path: str | Path) -> Optional[str]:
        """Parse a local PDF file path to plain text. Returns None on failure."""
        if not self.available:
            return None
        try:
            doc = self._fitz.open(str(pdf_path))
        except Exception as e:
            logger.info("pymupdf failed to open %s: %s", pdf_path, e)
            return None
        return self._extract_text(doc)

    def parse_bytes(self, pdf_bytes: bytes) -> Optional[str]:
        """Parse PDF bytes directly via an in-memory stream."""
        if not self.available or not pdf_bytes:
            return None
        try:
            doc = self._fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            logger.info("pymupdf failed to open byte stream: %s", e)
            return None
        return self._extract_text(doc)

    def _extract_text(self, doc) -> Optional[str]:
        try:
            chunks: list[str] = []
            running_total = 0
            for page in doc:
                page_text = page.get_text("text") or ""
                if not page_text:
                    continue
                chunks.append(page_text)
                running_total += len(page_text)
                if running_total >= self.max_chars:
                    break
            text = "\n\n".join(chunks).strip()
            return text[: self.max_chars] if text else None
        except Exception as e:
            logger.info("pymupdf text extraction failed: %s", e)
            return None
        finally:
            try:
                doc.close()
            except Exception:
                pass


# Backward-compatible alias: earlier drafts used MarkerPdfParser.
MarkerPdfParser = PyMuPdfParser


# ────────────────────────── Full-Text Resolver ━━━━━━━━━━━━━━━━━━━━━━━━

class FullTextResolver:
    """Resolve full text for an :class:`Article` via OA providers + PDF parsing.

    Resolution chain (first hit wins):
      1. Europe PMC OA full-text XML (when PMCID is known).
      2. bioRxiv / medRxiv direct PDF (when ``article.source`` is preprint).
      3. By DOI: Unpaywall → OpenAlex → Semantic Scholar (PDF URL discovery).
      4. Download the discovered PDF and parse with marker-pdf.

    Cached results are stored under namespace ``"resolved_fulltext"`` keyed by
    PMID (falling back to DOI).
    """

    def __init__(
        self,
        email: str = "",
        api_keys: Optional[Dict[str, str]] = None,
        cache: Optional[Cache] = None,
        pdf_parser: Optional[PyMuPdfParser] = None,
        max_chars: int = 30000,
        timeout: int = 60,
    ):
        api_keys = _resolve_api_keys(api_keys)
        # Stash the resolved dict so callers (e.g. compare-mode sub-pipelines)
        # can thread the same credentials to a fresh resolver without
        # re-reading env vars themselves.
        self.api_keys = api_keys
        # Email falls back to SYNTHSCHOLAR_EMAIL env var, then NCBI_EMAIL default.
        self.email = _resolve_email(email)
        self.cache = cache
        self.max_chars = max_chars
        self.unpaywall = UnpaywallProvider(email=self.email)
        self.openalex = OpenAlexProvider(email=self.email)
        self.semantic_scholar = SemanticScholarProvider(
            email=self.email, api_key=api_keys.get("semantic_scholar"),
        )
        self.europe_pmc = EuropePMCProvider(email=self.email)
        self.biorxiv = BioRxivClient(server="biorxiv")
        self.medrxiv = BioRxivClient(server="medrxiv")
        self.pdf_parser = pdf_parser or PyMuPdfParser(max_chars=max_chars)
        self.client = httpx.Client(
            timeout=timeout, follow_redirects=True,
            headers={"User-Agent": f"SynthScholar/1.0 (mailto:{self.email})"},
        )

    # ── public API ──

    def resolve(self, article: Article) -> Optional[str]:
        """Return full text for an article, or None if unresolvable."""
        if article.full_text:
            return article.full_text

        cache_key = article.pmid or article.doi or article.title[:60]
        if self.cache and cache_key:
            c = self.cache.get("resolved_fulltext", cache_key)
            if c:
                return c.get("text")

        text = (
            self._try_europe_pmc(article)
            or self._try_preprint_pdf(article)
            or self._try_doi_chain(article)
        )

        if text:
            text = text[: self.max_chars]
            article.full_text = text
            if self.cache and cache_key:
                self.cache.set("resolved_fulltext", cache_key, {"text": text})
        return text

    # ── individual strategies ──

    def _try_europe_pmc(self, article: Article) -> Optional[str]:
        pmcid = article.pmc_id
        if not pmcid and article.doi:
            pub = self.europe_pmc.get_by_doi(article.doi)
            if pub:
                pmcid = pub.external_ids.get("PMCID")
        if not pmcid:
            return None
        xml = self.europe_pmc.fetch_full_text_xml(pmcid)
        if not xml:
            return None
        body = re.findall(r"<body>(.*?)</body>", xml, re.DOTALL)
        if not body:
            return None
        text = re.sub(r"<[^>]+>", " ", body[0])
        return re.sub(r"\s+", " ", text).strip() or None

    def _try_preprint_pdf(self, article: Article) -> Optional[str]:
        if not article.doi:
            return None
        src = (article.source or "").lower()
        if src == "biorxiv":
            url = self.biorxiv.pdf_url(article.doi)
        elif src == "medrxiv":
            url = self.medrxiv.pdf_url(article.doi)
        else:
            return None
        return self._download_and_parse(url) if url else None

    def _try_doi_chain(self, article: Article) -> Optional[str]:
        if not article.doi:
            return None
        for provider in (self.unpaywall, self.openalex, self.semantic_scholar):
            try:
                pub = provider.get_by_doi(article.doi)
            except Exception as e:
                logger.info("[%s] DOI lookup failed: %s", provider.name, e)
                continue
            if pub and pub.pdf_url:
                text = self._download_and_parse(pub.pdf_url)
                if text:
                    return text
        return None

    def _download_and_parse(self, url: str) -> Optional[str]:
        """Download a PDF URL and parse it; defensive against Cloudflare-style
        challenge pages that arrive as HTML masquerading as a PDF endpoint.

        Returns ``None`` (and logs at INFO) when:
          * HTTP 403 or 503 — typically a Cloudflare bot-protection challenge.
          * Any non-200 status.
          * ``Content-Type`` is HTML or not a PDF MIME type.
          * The first five bytes are not ``%PDF-`` — this catches HTML
            challenge pages even when the server lies about the content type.
        """
        if not self.pdf_parser.available:
            return None
        try:
            r = self.client.get(url)
        except httpx.HTTPError as e:
            logger.info("PDF download failed for %s: %s", url, e)
            return None

        if r.status_code in (403, 503):
            logger.info(
                "PDF blocked (HTTP %s) — likely Cloudflare bot-protection challenge: %s",
                r.status_code, url,
            )
            return None
        if r.status_code != 200:
            logger.info("PDF download failed (HTTP %s): %s", r.status_code, url)
            return None

        content_type = r.headers.get("content-type", "").lower()
        if "html" in content_type:
            logger.info(
                "Got HTML, not PDF (content-type=%s) — likely a challenge page: %s",
                content_type, url,
            )
            return None

        body = r.content
        if not body or body[:5] != b"%PDF-":
            logger.info(
                "Response is not a PDF (first bytes=%r, content-type=%s): %s",
                body[:8] if body else b"", content_type, url,
            )
            return None

        return self.pdf_parser.parse_bytes(body)
