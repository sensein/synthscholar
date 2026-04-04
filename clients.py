"""
API clients for PubMed (NCBI E-utilities), bioRxiv, and SQLite cache.

These are plain HTTP clients with no LLM dependency — they handle
data acquisition and caching for the PRISMA pipeline.
"""

from __future__ import annotations

import re
import json
import time
import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx

from models import Article


# ────────────────────────── Configuration ──────────────────────────────

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIORXIV_API = "https://api.biorxiv.org/details/biorxiv"
NCBI_TOOL = "prisma_review_agent"
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
