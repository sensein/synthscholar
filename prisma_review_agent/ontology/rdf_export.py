"""
RDF export for PRISMA review results.

Serializes a PRISMAReviewResult to Turtle (.ttl) or JSON-LD (.jsonld)
following the SLR Ontology (slr-ontology v0.1.0).

Mapping summary
---------------
PRISMAReviewResult  →  slr:SystematicReview  (prov:Activity)
Article             →  slr:IncludedSource    (fabio:Expression)
StudyDataExtraction →  slr:ChartingRecord    (sections B / C / F / G)
RiskOfBiasResult    →  slr:RiskOfBiasAssessment
CriticalAppraisalRubric (if present) → slr:CriticalAppraisal (four domains)
EvidenceSpan (grounded=True) → oa:Annotation
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from rdflib import Graph, Literal, BNode, URIRef
from rdflib.namespace import RDF, XSD

from .namespaces import (
    SLR, PROV, DCTERMS, FABIO, BIBO, OA, SCHEMA,
    bind_namespaces, article_uri, review_uri, DCT,
)

if TYPE_CHECKING:
    from prisma_review_agent.models import PRISMAReviewResult, Article, EvidenceSpan


# ── helpers ───────────────────────────────────────────────────────────────────

def _lit(value: str | int | float | None, datatype=None) -> Literal | None:
    """Return a typed Literal, or None if value is falsy."""
    if value is None or value == "":
        return None
    if datatype:
        return Literal(value, datatype=datatype)
    return Literal(str(value))


def _add(graph: Graph, subject, predicate, obj) -> None:
    """Add triple only when obj is not None."""
    if obj is not None:
        graph.add((subject, predicate, obj))


# ── US1: SystematicReview + IncludedSource ────────────────────────────────────

def _add_software_agent(g: Graph, model_name: str) -> BNode:
    """Create a prov:SoftwareAgent blank node for the PRISMA agent."""
    agent = BNode()
    g.add((agent, RDF.type, PROV.SoftwareAgent))
    g.add((agent, SCHEMA.name, Literal("PRISMA Agent")))
    if model_name:
        g.add((agent, SLR.model_name, Literal(model_name)))
    return agent


def _add_included_source(
    g: Graph,
    article: "Article",
    review_node: URIRef,
) -> URIRef:
    """Add slr:IncludedSource triples for one article; return its URI."""
    uri = article_uri(article)
    g.add((uri, RDF.type, SLR.IncludedSource))
    g.add((uri, RDF.type, FABIO.Expression))

    _add(g, uri, DCTERMS.title,              _lit(article.title))
    _add(g, uri, BIBO.pmid,                  _lit(article.pmid))
    _add(g, uri, BIBO.doi,                   _lit(article.doi))
    _add(g, uri, DCTERMS.bibliographicCitation, _lit(article.citation))

    if article.year:
        try:
            g.add((uri, FABIO.hasPublicationYear,
                   Literal(int(article.year), datatype=XSD.integer)))
        except ValueError:
            _add(g, uri, FABIO.hasPublicationYear, _lit(article.year))

    if article.authors:
        for author in article.authors.split(";"):
            author = author.strip()
            if author:
                g.add((uri, DCTERMS.creator, Literal(author)))

    _add(g, uri, SLR.journal, _lit(article.journal))

    g.add((review_node, SLR.included_sources, uri))
    return uri


# ── US2a: ChartingRecord (from StudyDataExtraction) ──────────────────────────

def _add_charting(g: Graph, source_uri: URIRef, article: "Article") -> None:
    """Map StudyDataExtraction → slr:ChartingRecord (sections B / C / F / G)."""
    ext = article.extracted_data
    if ext is None:
        return

    rec = BNode()
    g.add((rec, RDF.type, SLR.ChartingRecord))
    g.add((source_uri, SLR.charting_record, rec))

    # Section B — Study Design
    if ext.study_design and ext.study_design != "Unknown":
        sec_b = BNode()
        g.add((sec_b, RDF.type, SLR.StudyDesign))
        g.add((sec_b, SLR.study_design, Literal(ext.study_design)))
        _add(g, sec_b, SLR.country_region, _lit(ext.follow_up))  # closest proxy
        g.add((rec, SLR.section_b_design, sec_b))

    # Section C — Primary Sample
    if ext.population or ext.sample_size:
        sec_c = BNode()
        g.add((sec_c, RDF.type, SLR.PrimarySample))
        _add(g, sec_c, SLR.population_studied, _lit(ext.population))
        _add(g, sec_c, SLR.sample_size_text,   _lit(ext.sample_size))
        g.add((rec, SLR.section_c_primary_sample, sec_c))

    # Section F — Methods and Results
    if ext.key_findings or ext.outcomes or ext.effect_measures:
        sec_f = BNode()
        g.add((sec_f, RDF.type, SLR.MethodsAndResults))
        for kf in ext.key_findings:
            g.add((sec_f, SLR.key_results, Literal(kf)))
        for oc in ext.outcomes:
            g.add((sec_f, SLR.outcome, Literal(oc)))
        for em in ext.effect_measures:
            g.add((sec_f, SLR.effect_measure, Literal(em)))
        g.add((rec, SLR.section_f_methods_results, sec_f))

    # Section G — Synthesis Fields
    sec_g = BNode()
    g.add((sec_g, RDF.type, SLR.SynthesisFields))
    added_g = False
    if ext.key_findings:
        g.add((sec_g, SLR.summary_of_findings, Literal("; ".join(ext.key_findings[:3]))))
        added_g = True
    if ext.funding:
        g.add((sec_g, SLR.reviewer_notes, Literal(f"Funding: {ext.funding}")))
        added_g = True
    if added_g:
        g.add((rec, SLR.section_g_synthesis, sec_g))


# ── US2b: RiskOfBiasAssessment ────────────────────────────────────────────────

def _concern_from_judgment(judgment_value: str) -> str:
    """Map RoBJudgment string to ConcernLevelEnum value."""
    mapping = {
        "Low": "low",
        "Some concerns": "some",
        "High": "high",
        "Unclear": "unclear",
    }
    return mapping.get(judgment_value, "unclear")


def _add_rob(
    g: Graph,
    source_uri: URIRef,
    article: "Article",
    rob_tool_name: str = "RoB 2",
) -> None:
    """Map RiskOfBiasResult → slr:RiskOfBiasAssessment."""
    rob = article.risk_of_bias
    if rob is None:
        return

    rob_node = BNode()
    g.add((rob_node, RDF.type, SLR.RiskOfBiasAssessment))
    g.add((rob_node, SLR.tool_used, Literal(rob_tool_name)))
    g.add((rob_node, SLR.overall_judgment,
           Literal(_concern_from_judgment(rob.overall.value))))

    for domain in rob.assessments:
        d_node = BNode()
        g.add((d_node, RDF.type, SLR.RoBDomainJudgment))
        g.add((d_node, SLR.domain_name, Literal(domain.domain)))
        g.add((d_node, SLR.judgment,
               Literal(_concern_from_judgment(domain.judgment.value))))
        _add(g, d_node, SLR.supporting_quote, _lit(domain.support))
        g.add((rob_node, SLR.domain_judgments, d_node))

    g.add((source_uri, SLR.risk_of_bias_assessment, rob_node))


# ── US2c: CriticalAppraisal (optional — root-level model only) ───────────────

def _add_appraisal_from_dict(
    g: Graph,
    source_uri: URIRef,
    appraisal_dict: dict,
) -> None:
    """Map a CriticalAppraisalRubric-like dict to slr:CriticalAppraisal.

    Accepts the dict form of the model so this module doesn't need to import
    the root-level models.py at runtime. Called from pipeline if available.
    """
    appraisal_node = BNode()
    g.add((appraisal_node, RDF.type, SLR.CriticalAppraisal))

    domain_map = {
        "domain_1_participant_quality":   ("SamplePopulationDomain",  SLR.domain_1_sample),
        "domain_2_data_collection_quality": ("DataCollectionDomain",  SLR.domain_2_data_collection),
        "domain_3_feature_model_quality": ("MethodsAnalysisDomain",   SLR.domain_3_methods),
        "domain_4_bias_transparency":     ("BiasTransparencyDomain",  SLR.domain_4_bias_transparency),
    }
    for field, (rdf_class, predicate) in domain_map.items():
        domain_data = appraisal_dict.get(field)
        if not domain_data:
            continue
        d_node = BNode()
        g.add((d_node, RDF.type, getattr(SLR, rdf_class)))
        concern = domain_data.get("overall_concern", "")
        if concern:
            g.add((d_node, SLR.overall_concern, Literal(concern.lower())))
        for item in domain_data.get("items", []):
            item_node = BNode()
            g.add((item_node, RDF.type, SLR.AppraisalItem))
            _add(g, item_node, SLR.item_text, _lit(item.get("item_text")))
            _add(g, item_node, SLR.rating,    _lit(item.get("rating")))
            _add(g, item_node, SLR.justification, _lit(item.get("notes")))
            g.add((d_node, SLR.items, item_node))
        g.add((appraisal_node, predicate, d_node))

    g.add((source_uri, SLR.appraisal, appraisal_node))


# ── US3: EvidenceSpan → oa:Annotation ────────────────────────────────────────

def _add_evidence_spans(
    g: Graph,
    review_node: URIRef,
    result: "PRISMAReviewResult",
    pmid_to_uri: dict[str, URIRef],
) -> None:
    """Emit grounded evidence spans as oa:Annotation nodes."""
    for span in result.evidence_spans:
        if not span.grounded:
            continue

        ann = BNode()
        g.add((ann, RDF.type, OA.Annotation))

        body = BNode()
        g.add((body, RDF.type, DCT.Text))
        g.add((body, RDF.value, Literal(span.text)))
        g.add((ann, OA.hasBody, body))

        target_uri = pmid_to_uri.get(span.paper_pmid)
        if target_uri:
            g.add((ann, OA.hasTarget, target_uri))
        else:
            g.add((ann, OA.hasTarget, Literal(span.paper_pmid)))

        _add(g, ann, SLR.relevance_score,
             Literal(round(span.relevance_score, 4), datatype=XSD.float))
        _add(g, ann, SLR.claim_label, _lit(span.claim))

        g.add((review_node, SLR.evidence_spans, ann))


# ── Master graph builder ──────────────────────────────────────────────────────

def _build_graph(result: "PRISMAReviewResult") -> Graph:
    """Build the full RDF graph from a PRISMAReviewResult."""
    g = Graph()
    bind_namespaces(g)

    rev_uri = review_uri(result.protocol)

    # ── slr:SystematicReview ──
    g.add((rev_uri, RDF.type, SLR.SystematicReview))
    g.add((rev_uri, RDF.type, PROV.Activity))
    _add(g, rev_uri, DCTERMS.title,     _lit(result.protocol.title))
    _add(g, rev_uri, SLR.research_question, _lit(result.research_question))
    _add(g, rev_uri, SLR.reporting_standard, Literal("PRISMA_2020"))

    if result.timestamp:
        g.add((rev_uri, PROV.generatedAtTime,
               Literal(result.timestamp[:10], datatype=XSD.date)))

    # prov:SoftwareAgent
    model_name = getattr(result.protocol, "model_name", "") or ""
    agent_node = _add_software_agent(g, model_name)
    activity = BNode()
    g.add((activity, RDF.type, PROV.Activity))
    g.add((activity, PROV.wasAssociatedWith, agent_node))
    g.add((rev_uri, PROV.wasGeneratedBy, activity))

    # cache provenance
    if result.cache_hit:
        matched_title = result.cache_matched_criteria.get("title", "")
        if matched_title:
            g.add((rev_uri, PROV.wasDerivedFrom, Literal(matched_title)))
        g.add((rev_uri, SLR.cache_hit, Literal(True, datatype=XSD.boolean)))
        g.add((rev_uri, SLR.cache_similarity_score,
               Literal(round(result.cache_similarity_score, 4), datatype=XSD.float)))

    # PRISMA flow counts
    f = result.flow
    g.add((rev_uri, SLR.total_identified,
           Literal(f.total_identified, datatype=XSD.integer)))
    g.add((rev_uri, SLR.duplicates_removed,
           Literal(f.duplicates_removed, datatype=XSD.integer)))
    g.add((rev_uri, SLR.included_synthesis,
           Literal(f.included_synthesis, datatype=XSD.integer)))

    # Search queries
    for q in result.search_queries:
        g.add((rev_uri, SLR.search_query, Literal(q)))

    # ── IncludedSources + per-article enrichment ──
    pmid_to_uri: dict[str, URIRef] = {}
    for article in result.included_articles:
        a_uri = _add_included_source(g, article, rev_uri)
        if article.pmid:
            pmid_to_uri[article.pmid] = a_uri
        _add_charting(g, a_uri, article)
        _add_rob(g, a_uri, article, rob_tool_name=result.protocol.rob_tool.value)
        if article.critical_appraisal is not None:
            _add_appraisal_from_dict(g, a_uri, article.critical_appraisal.model_dump())

    # ── EvidenceSpans → oa:Annotation ──
    _add_evidence_spans(g, rev_uri, result, pmid_to_uri)

    return g


# ── Public API ────────────────────────────────────────────────────────────────

def to_turtle(result: "PRISMAReviewResult") -> str:
    """Serialize a PRISMAReviewResult to Turtle format."""
    return _build_graph(result).serialize(format="turtle")


def to_jsonld(result: "PRISMAReviewResult") -> str:
    """Serialize a PRISMAReviewResult to JSON-LD format."""
    return _build_graph(result).serialize(format="json-ld", indent=2)
