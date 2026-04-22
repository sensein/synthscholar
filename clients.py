"""
API clients for PubMed (NCBI E-utilities), bioRxiv, and multi-source OA fetcher.

These are plain HTTP clients with no LLM dependency — they handle
data acquisition and caching for the PRISMA pipeline.

Supports parallel multi-source search:
- Semantic Scholar, PubMed, arXiv, CrossRef, OpenAlex, Unpaywall, CORE, DOAJ, Europe PMC
"""

from __future__ import annotations

import re
import json
import time
import sqlite3
import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List
from urllib.parse import quote_plus

import httpx
from concurrent.futures import ThreadPoolExecutor, as_completed

from models import Article


# ────────────────────────── Configuration ──────────────────────────────

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
NCBI_TOOL = "synthscholar"
NCBI_EMAIL = "user@example.com"
RATE_LIMIT_DELAY = 0.35


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


# ────────────────────────── bioRxiv Client ─────────────────────────────

class BioRxivClient:
    """bioRxiv preprint search via the public API."""

    def __init__(self, cache: Optional[Cache] = None):
        self.client = httpx.Client(timeout=15)
        self.cache = cache

    def search(self, query: str, max_results: int = 10,
               days_back: int = 180) -> list[Article]:
        cache_key = f"{query}_{days_back}"
        if self.cache:
            c = self.cache.get("biorxiv", cache_key)
            if c:
                return [Article(**a) for a in c["articles"]]
        try:
            today = datetime.now()
            start = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
            end = today.strftime("%Y-%m-%d")
            articles: list[Article] = []
            qw = set(query.lower().split())

            for cursor in range(0, 150, 30):
                r = self.client.get(f"{BIORXIV_API}/{start}/{end}/{cursor}/30")
                if r.status_code != 200:
                    break
                for item in r.json().get("collection", []):
                    combined = (
                        item.get("title", "") + " " + item.get("abstract", "")
                    ).lower()
                    score = sum(1 for w in qw if len(w) > 3 and w in combined)
                    if score >= 2:
                        articles.append(Article(
                            pmid=f"biorxiv_{item.get('doi', '').split('/')[-1]}",
                            title=item.get("title", ""),
                            abstract=item.get("abstract", ""),
                            authors=item.get("authors", ""),
                            journal="bioRxiv (Preprint)",
                            year=item.get("date", "")[:4],
                            doi=item.get("doi", ""),
                            source="biorxiv",
                        ))
                if len(articles) >= max_results:
                    break

            result = articles[:max_results]
            if self.cache:
                self.cache.set(
                    "biorxiv", cache_key,
                    {"articles": [a.model_dump() for a in result]},
                )
            return result
        except Exception:
            return []


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
            pmid=self.external_ids.get("PMID") or self.external_ids.get("arXiv") or 
                  self.external_ids.get("DOI") or f"{self.source}_{self.title[:30]}",
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
            "User-Agent": f"PRISMA-Agent/1.0 (mailto:{email or 'anonymous@example.com'})"
        })

    def _get(self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> Optional[dict]:
        try:
            r = self._session.get(url, params=params, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            time.sleep(self.rate_limit_sec)
            return r.json()
        except httpx.HTTPStatusError as e:
            print(f"[{self.name}] HTTP {e.response.status_code} on {url}")
        except httpx.RequestError as e:
            print(f"[{self.name}] request failed: {e}")
        except ValueError:
            print(f"[{self.name}] non-JSON response from {url}")
        return None

    def _get_raw(self, url: str, params: Optional[dict] = None) -> Optional[str]:
        try:
            r = self._session.get(url, params=params, timeout=self.timeout)
            r.raise_for_status()
            time.sleep(self.rate_limit_sec)
            return r.text
        except httpx.RequestError as e:
            print(f"[{self.name}] raw request failed: {e}")
            return None

    @abstractmethod
    def search(self, query: str, limit: int = 25) -> List[Publication]:
        ...


# ────────────────────────── OpenAlex Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━

class OpenAlexProvider(BaseOAProvider):
    """OpenAlex - comprehensive free scholarly graph."""
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

        results = []
        for w in data.get("results", []):
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

            results.append(Publication(
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
            ))
        return results

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
    """Europe PMC REST API - excellent for life sciences OA content."""
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

        results = []
        for item in (data.get("resultList") or {}).get("result", []):
            pmcid = item.get("pmcid")
            doi = item.get("doi")
            is_oa = item.get("isOpenAccess") == "Y" or item.get("inPMC") == "Y"
            pdf_url = None
            if pmcid:
                pdf_url = f"https://europepmc.org/articles/{pmcid}?pdf=render"

            try:
                year = int(item.get("pubYear")) if item.get("pubYear") else None
            except ValueError:
                year = None

            results.append(Publication(
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
            ))
        return results


# ────────────────────────── CrossRef Provider ━━━━━━━━━━━━━━━━━━━━━━━━━

class CrossRefProvider(BaseOAProvider):
    """CrossRef REST API - DOI-centric metadata."""
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

        results = []
        for item in (data.get("message") or {}).get("items", []):
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

            results.append(Publication(
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
            ))
        return results


# ────────────────────────── DOAJ Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DOAJProvider(BaseOAProvider):
    """Directory of Open Access Journals - article search."""
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
            doi = next((i.get("id") for i in b.get("identifier", []) if i.get("type") == "doi"), None)
            fulltext = next((l.get("url") for l in b.get("link", []) if l.get("type") == "fulltext"), None)
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

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        params = {"query": query, "limit": min(limit, 100), "fields": self.FIELDS}
        data = self._get(f"{self.BASE}/paper/search", params=params, headers=headers)
        if not data:
            return []
        results = []
        for p in data.get("data", []):
            ext = p.get("externalIds") or {}
            pdf = (p.get("openAccessPdf") or {}).get("url")
            results.append(Publication(
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
            ))
        return results


# ────────────────────────── arXiv Provider ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ArxivProvider(BaseOAProvider):
    """arXiv API (Atom XML via feedparser)."""
    name = "arxiv"
    BASE = "http://export.arxiv.org/api/query"
    rate_limit_sec = 3.0

    def search(self, query: str, limit: int = 25) -> List[Publication]:
        try:
            import feedparser
        except ImportError:
            print("feedparser not installed; run: pip install feedparser")
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
    """CORE API v3 - worldwide OA aggregator (requires free API key)."""
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
            print(f"[core] request failed: {e}")
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
    """Unpaywall - OA lookup by DOI and keyword search (email required)."""
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
        results = []
        for item in (data.get("results") or [])[:limit]:
            r = item.get("response") or {}
            best_oa = r.get("best_oa_location") or {}
            results.append(Publication(
                source=self.name,
                title=r.get("title") or "",
                authors=[
                    f"{a.get('given','')} {a.get('family','')}".strip()
                    for a in (r.get("z_authors") or [])
                ],
                year=r.get("year"),
                doi=r.get("doi"),
                url=r.get("doi_url"),
                pdf_url=best_oa.get("url_for_pdf") or best_oa.get("url"),
                open_access=bool(r.get("is_oa")),
                venue=r.get("journal_name"),
                external_ids={"DOI": r.get("doi", "")},
            ))
        return results


# ────────────────────────── PubMed OA Provider ━━━━━━━━━━━━━━━━━━━━━━━━━

class PubMedOAProvider(BaseOAProvider):
    """PubMed via NCBI E-utilities, returning Publication records."""
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
            pdf_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/pdf/" if pmcid else None
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

    def __init__(self, email: Optional[str] = None, api_keys: Optional[Dict[str, str]] = None, cache: Optional[Cache] = None):
        api_keys = api_keys or {}
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
            print(f"Unknown source: {name}")

        all_results: List[Publication] = []

        def _search_one(name: str) -> List[Publication]:
            try:
                return self.providers[name].search(query, limit=limit_per_source)
            except Exception as e:
                print(f"[{name}] search failed: {e}")
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
        seen = {}
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
