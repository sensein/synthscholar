# SLR Ontology

SynthScholar ships with a first-class **Systematic Literature Review (SLR)
Ontology** — a [LinkML](https://linkml.io/) schema that gives every review
a stable, machine-readable, linked-data representation.

- **Namespace:** `https://w3id.org/slr-ontology/` (prefix `slr:`)
- **Version:** 0.2.0
- **Source:** [`synthscholar/ontology/slr_ontology.yaml`](https://github.com/tekrajchhetri/synthscholar/blob/main/synthscholar/ontology/slr_ontology.yaml)
- **Serialisations:** LinkML YAML · OWL/Turtle · JSON Schema

## Downloads

```{raw} html
<div class="download-grid">
  <a class="download-card" href="_static/slr_ontology.owl.ttl" download>
    <div class="download-icon">📘</div>
    <div class="download-title">OWL / Turtle</div>
    <div class="download-desc">Load into Protégé, GraphDB, Fuseki, or any RDF store</div>
    <div class="download-ext">slr_ontology.owl.ttl · 214 KB</div>
  </a>
  <a class="download-card" href="_static/slr_ontology.yaml" download>
    <div class="download-icon">📗</div>
    <div class="download-title">LinkML YAML</div>
    <div class="download-desc">Source of truth — edit this file and regenerate the others</div>
    <div class="download-ext">slr_ontology.yaml · 63 KB</div>
  </a>
  <a class="download-card" href="_static/slr_ontology.schema.json" download>
    <div class="download-icon">📙</div>
    <div class="download-title">JSON Schema</div>
    <div class="download-desc">Validate JSON-LD exports or generate typed clients</div>
    <div class="download-ext">slr_ontology.schema.json · 125 KB</div>
  </a>
</div>
```

## Why an Ontology?

Typed JSON is enough to render a UI, but it isn't enough to:

- **Interlink reviews** — share protocols, studies, and evidence across institutions without re-key-mapping
- **Provenance-trace every LLM decision** — which model produced which charting cell, at what time, from which source span
- **Federate over SPARQL** — query "all reviews that used RoB 2 and include an RCT with > 500 participants" across many reviews at once
- **Satisfy FAIR / Open Science requirements** — reviews become findable, accessible, interoperable, reusable

Every `PRISMAReviewResult` can be exported to Turtle or JSON-LD and loaded
into any RDF store (Oxigraph, GraphDB, Blazegraph, Virtuoso, Fuseki, etc.).

## Reused Vocabularies

| Prefix | Namespace | Used for |
|--------|-----------|----------|
| `slr` | `https://w3id.org/slr-ontology/` | Review-specific classes & properties |
| `prov` | `http://www.w3.org/ns/prov#` | Activity/Agent/Entity provenance (W3C PROV-O) |
| `dcterms` | `http://purl.org/dc/terms/` | Title, creator, citation, date |
| `fabio` | `http://purl.org/spar/fabio/` | Bibliographic expression classes |
| `bibo` | `http://purl.org/ontology/bibo/` | PMID, DOI, ISSN |
| `oa` | `http://www.w3.org/ns/oa#` | Open Annotation for evidence spans |
| `schema` | `http://schema.org/` | Generic entity descriptors |
| `foaf` | `http://xmlns.com/foaf/0.1/` | Person / agent metadata |
| `skos` | `http://www.w3.org/2004/02/skos/core#` | Concept hierarchies for enums |

This means SynthScholar exports are **not a bespoke format** — they're
interoperable with every tool that already understands these standard
vocabularies.

## Core Class Map

```{mermaid}
classDiagram
    class SystematicReview {
        +research_question
        +reporting_standard: PRISMA_2020
        +total_identified: int
        +included_synthesis: int
        +search_query: list
    }
    class IncludedSource {
        +title
        +pmid
        +doi
        +year
        +citation
    }
    class ChartingRecord {
        +section_b_design
        +section_c_primary_sample
        +section_f_methods_results
        +section_g_synthesis
    }
    class RiskOfBiasAssessment {
        +tool_used: RoB2|ROBINS-I|...
        +overall_judgment
        +domain_judgments
    }
    class CriticalAppraisal {
        +domain_1_sample
        +domain_2_data_collection
        +domain_3_methods
        +domain_4_bias_transparency
    }
    class Annotation {
        +hasBody: text
        +hasTarget: IncludedSource
        +relevance_score
        +claim_label
    }
    class ModelInvocation {
        +model_name
        +prompt
        +tool_invocations
        +generatedAtTime
    }
    class SoftwareAgent {
        +model_name
        +name
    }

    SystematicReview "1" --> "*" IncludedSource : slr:included_sources
    SystematicReview "1" --> "*" Annotation : slr:evidence_spans
    SystematicReview "1" --> "1" SoftwareAgent : prov:wasGeneratedBy
    IncludedSource "1" --> "0..1" ChartingRecord : slr:charting_record
    IncludedSource "1" --> "0..1" RiskOfBiasAssessment : slr:risk_of_bias_assessment
    IncludedSource "1" --> "0..1" CriticalAppraisal : slr:appraisal
    ModelInvocation "*" --> "1" SoftwareAgent : prov:wasAssociatedWith
```

## How Pipeline Data Maps to RDF

| Pipeline object | RDF class | Notes |
|-----------------|-----------|-------|
| `PRISMAReviewResult` | `slr:SystematicReview` · `prov:Activity` | Review-level metadata + PRISMA flow counts |
| `Article` (included) | `slr:IncludedSource` · `fabio:Expression` | URI priority: PMID → DOI → title hash |
| `StudyDataExtraction` | `slr:ChartingRecord` | Split into sections B (design), C (sample), F (methods/results), G (synthesis) |
| `RiskOfBiasResult` | `slr:RiskOfBiasAssessment` | Per-domain `slr:RoBDomainJudgment` |
| `CriticalAppraisalRubric` | `slr:CriticalAppraisal` | 4 domains → `slr:AppraisalDomain` subclasses |
| `EvidenceSpan` (grounded) | `oa:Annotation` | `oa:hasBody` = text, `oa:hasTarget` = source |
| Model name / API key | `prov:SoftwareAgent` | `slr:model_name` literal |
| Cache hit | `prov:wasDerivedFrom` | Plus `slr:cache_similarity_score` |

Only **grounded** evidence spans are serialised — ungrounded spans (those
that failed the fuzzy-matching validator) never enter the RDF output.

## Enumerations

The schema defines 30+ controlled vocabularies, including:

| Enum | Values (abbreviated) |
|------|----------------------|
| `RoBToolEnum` | `RoB_2`, `ROBINS_I`, `ROBINS_E`, `Newcastle_Ottawa`, `QUADAS_2`, `CASP`, `JBI`, `Murad_Tool`, `Jadad` |
| `ConcernLevelEnum` | `low`, `some`, `high`, `unclear` |
| `ItemRatingEnum` | `yes`, `partial`, `no`, `not_applicable`, `not_reported` |
| `StudyDesignEnum` | `RCT`, `cohort`, `case_control`, `cross_sectional`, `qualitative`, `systematic_review`, ... |
| `ReportingStandardEnum` | `PRISMA_2020`, `PRISMA_P`, `PRISMA_ScR`, `MOOSE`, `STROBE`, ... |
| `DatabaseEnum` | `PubMed`, `Embase`, `Cochrane`, `bioRxiv`, `Scopus`, `WoS`, ... |
| `EffectMeasureEnum` | `OR`, `RR`, `HR`, `MD`, `SMD`, `RD`, `AUC`, ... |
| `CertaintyOfEvidenceToolEnum` | `GRADE`, `CERQual`, `GRADE_CERQual`, ... |

Using enums (instead of free-text strings) lets you run consistent queries
across heterogeneous reviews.

## Provenance — AI/LLM Interactions

One design goal of the ontology is **first-class LLM provenance**. Each
structured output carries (or can carry) a trail back to:

- The exact **prompt** (`slr:Prompt`)
- The **model configuration** (`slr:ModelConfiguration` — model name, temperature, max tokens)
- The **model invocation** (`slr:ModelInvocation` — inputs, outputs, timestamps)
- Any **tool invocations** made by the model (`slr:ToolInvocation`)
- **User inputs** and **review events** (`slr:UserInput`, `slr:ReviewEvent`)

This is how you answer questions like *"Was this charting cell produced by
Claude or GPT-4o?"* or *"Which prompt revision was live when this risk-of-bias
judgment was recorded?"* — the provenance is in the graph itself, not a side
log.

## Exporting

### From Python

```python
from synthscholar import PRISMAReviewPipeline, ReviewProtocol
from synthscholar.ontology import to_turtle, to_jsonld

protocol = ReviewProtocol(title="...", inclusion_criteria="...", exclusion_criteria="...")
pipeline = PRISMAReviewPipeline(protocol=protocol, api_key="sk-or-...")
result = await pipeline.run(auto=True)

# Turtle (.ttl)
ttl = to_turtle(result)

# JSON-LD (.jsonld)
jsonld = to_jsonld(result)

from pathlib import Path
Path("review.ttl").write_text(ttl)
Path("review.jsonld").write_text(jsonld)
```

### From the CLI

```bash
synthscholar --title "..." --inclusion "..." --exclusion "..." \
  --export md json ttl jsonld \
  --auto
```

### Into an RDF store

```python
from synthscholar import to_oxigraph_store

store = to_oxigraph_store(result)     # returns pyoxigraph.Store
store.dump("review.ttl", "text/turtle")

# SPARQL queries
for row in store.query("""
    PREFIX slr: <https://w3id.org/slr-ontology/>
    PREFIX bibo: <http://purl.org/ontology/bibo/>

    SELECT ?title ?pmid WHERE {
      ?review slr:included_sources ?src .
      ?src    bibo:pmid ?pmid ;
              <http://purl.org/dc/terms/title> ?title .
    }
"""):
    print(row.title, row.pmid)
```

## Example Turtle Output

```turtle
@prefix slr:     <https://w3id.org/slr-ontology/> .
@prefix prov:    <http://www.w3.org/ns/prov#> .
@prefix dcterms: <http://purl.org/dc/terms/> .
@prefix fabio:   <http://purl.org/spar/fabio/> .
@prefix bibo:    <http://purl.org/ontology/bibo/> .
@prefix oa:      <http://www.w3.org/ns/oa#> .
@prefix xsd:     <http://www.w3.org/2001/XMLSchema#> .

<urn:uuid:f2f1a…> a slr:SystematicReview, prov:Activity ;
    dcterms:title            "Machine learning for sepsis prediction" ;
    slr:reporting_standard   "PRISMA_2020" ;
    slr:research_question    "Can ML predict sepsis onset in adult ICU patients?" ;
    slr:total_identified     412 ;
    slr:included_synthesis   45 ;
    slr:search_query         "sepsis AND (machine learning OR deep learning)" ;
    slr:included_sources     <https://pubmed.ncbi.nlm.nih.gov/34567890/> ;
    prov:generatedAtTime     "2026-04-22"^^xsd:date ;
    prov:wasGeneratedBy      [ a prov:Activity ;
                               prov:wasAssociatedWith [ a prov:SoftwareAgent ;
                                                        slr:model_name "anthropic/claude-sonnet-4" ] ] .

<https://pubmed.ncbi.nlm.nih.gov/34567890/> a slr:IncludedSource, fabio:Expression ;
    dcterms:title "Early warning model for sepsis using deep learning" ;
    bibo:pmid     "34567890" ;
    bibo:doi      "10.1038/s41598-022-xxxxx" ;
    fabio:hasPublicationYear 2022 ;
    slr:risk_of_bias_assessment [ a slr:RiskOfBiasAssessment ;
                                  slr:tool_used        "RoB 2" ;
                                  slr:overall_judgment "some" ] ;
    slr:charting_record [ a slr:ChartingRecord ;
                          slr:section_b_design  [ slr:study_design "Retrospective cohort" ] ;
                          slr:section_c_primary_sample [ slr:sample_size_text "12,458 ICU admissions" ] ] .
```

## Schema Artefacts

All three artefacts ship inside the Python package at
`synthscholar/ontology/`:

| File | Format | Use |
|------|--------|-----|
| `slr_ontology.yaml` | LinkML YAML | **Source of truth** — edit here |
| `slr_ontology.owl.ttl` | OWL/Turtle | Load into Protégé, TopBraid, GraphDB |
| `slr_ontology.schema.json` | JSON Schema | Validate JSON-LD exports, generate typed clients |

The OWL and JSON-Schema files are generated from the LinkML source with:

```bash
gen-owl       synthscholar/ontology/slr_ontology.yaml 2>/dev/null > slr_ontology.owl.ttl
gen-json-schema synthscholar/ontology/slr_ontology.yaml > slr_ontology.schema.json
# Note: redirecting stderr (2>/dev/null) is required — gen-owl writes non-fatal
# warnings to stderr that otherwise get mixed into the file and break Turtle parsing.
```

## Further Reading

- [LinkML documentation](https://linkml.io/linkml/)
- [W3C PROV-O primer](https://www.w3.org/TR/prov-primer/)
- [W3C Web Annotation Data Model](https://www.w3.org/TR/annotation-model/) (used for evidence spans)
- [FaBiO / BIBO / SPAR ontologies](http://www.sparontologies.net/)
