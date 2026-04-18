"""Shared RDF namespace constants and URI-minting helpers for the SLR Ontology."""

from __future__ import annotations

import hashlib
import uuid

from rdflib import Namespace, URIRef, BNode
from rdflib.namespace import RDF, RDFS, OWL, XSD  # noqa: F401 — re-exported for callers

# ── Ontology namespaces ────────────────────────────────────────────────────────
SLR    = Namespace("https://w3id.org/slr-ontology/")
PROV   = Namespace("http://www.w3.org/ns/prov#")
DCTERMS = Namespace("http://purl.org/dc/terms/")
FABIO  = Namespace("http://purl.org/spar/fabio/")
BIBO   = Namespace("http://purl.org/ontology/bibo/")
OA     = Namespace("http://www.w3.org/ns/oa#")
SCHEMA = Namespace("http://schema.org/")
FOAF   = Namespace("http://xmlns.com/foaf/0.1/")

# Convenience alias used widely in the schema
DCT = DCTERMS


def article_uri(article) -> URIRef:
    """Mint a stable URI for an article.

    Priority: PMID > DOI > title-hash blank substitute.
    """
    if article.pmid:
        return URIRef(f"https://pubmed.ncbi.nlm.nih.gov/{article.pmid}/")
    if article.doi:
        doi = article.doi.lstrip("https://doi.org/").lstrip("doi:")
        return URIRef(f"https://doi.org/{doi}")
    slug = hashlib.md5(article.title.encode()).hexdigest()[:12]
    return URIRef(f"urn:slr:article:{slug}")


def review_uri(protocol) -> URIRef:
    """Return the review URI: use protocol.review_id if set, else mint a UUID URI."""
    if protocol.review_id:
        return URIRef(protocol.review_id)
    return URIRef(f"urn:uuid:{uuid.uuid4()}")


def bind_namespaces(graph) -> None:
    """Bind all SLR ontology prefixes onto an rdflib Graph."""
    graph.bind("slr",     SLR)
    graph.bind("prov",    PROV)
    graph.bind("dcterms", DCTERMS)
    graph.bind("fabio",   FABIO)
    graph.bind("bibo",    BIBO)
    graph.bind("oa",      OA)
    graph.bind("schema",  SCHEMA)
    graph.bind("foaf",    FOAF)
