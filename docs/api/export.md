# Export Functions

All export functions are pure — they take a result object and return a string
(or a pyoxigraph `Store`). No side effects.

## Import

```python
from synthscholar import (
    to_markdown, to_json, to_bibtex,
    to_turtle, to_jsonld,
    to_charting_markdown, to_charting_json,
    to_appraisal_markdown, to_appraisal_json,
    to_rubric_markdown, to_rubric_json,
    to_compare_markdown, to_compare_json,
    to_compare_charting_markdown, to_compare_charting_json,
    to_narrative_summary_markdown, to_narrative_summary_json,
    to_oxigraph_store,
)
```

## Standard Review Exports

| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `to_markdown(result)` | `PRISMAReviewResult` | `str` | Full PRISMA 2020 Markdown document |
| `to_json(result)` | `PRISMAReviewResult` | `str` | Complete result as JSON |
| `to_bibtex(result)` | `PRISMAReviewResult` | `str` | BibTeX entries for all included articles |
| `to_turtle(result)` | `PRISMAReviewResult` | `str` | Turtle RDF using SLR Ontology |
| `to_jsonld(result)` | `PRISMAReviewResult` | `str` | JSON-LD RDF |

## Charting & Appraisal Exports

| Function | Description |
|----------|-------------|
| `to_charting_markdown(result)` | Data charting rubric as Markdown table |
| `to_charting_json(result)` | Data charting as JSON |
| `to_appraisal_markdown(result)` | Critical appraisal domains as Markdown |
| `to_appraisal_json(result)` | Critical appraisal as JSON |
| `to_rubric_markdown(result)` | Combined rubric (charting + appraisal) as Markdown |
| `to_rubric_json(result)` | Combined rubric as JSON |

## Narrative Summary Exports

| Function | Description |
|----------|-------------|
| `to_narrative_summary_markdown(result)` | PRISMA narrative rows as Markdown table |
| `to_narrative_summary_json(result)` | Narrative rows as JSON |

## Compare Mode Exports

| Function | Input | Description |
|----------|-------|-------------|
| `to_compare_markdown(result)` | `CompareReviewResult` | Side-by-side model comparison Markdown |
| `to_compare_json(result)` | `CompareReviewResult` | Full compare result as JSON |
| `to_compare_charting_markdown(result)` | `CompareReviewResult` | Per-model charting Markdown |
| `to_compare_charting_json(result)` | `CompareReviewResult` | Per-model charting JSON |

## RDF Store

```python
store = to_oxigraph_store(result)
# Returns a pyoxigraph.Store — use for SPARQL queries or Turtle serialisation
store.dump("output.ttl", "text/turtle")
```

## Writing to Files

```python
from pathlib import Path
from synthscholar import to_markdown, to_json, to_bibtex, to_turtle

base = Path("review_output")
base.with_suffix(".md").write_text(to_markdown(result))
base.with_suffix(".json").write_text(to_json(result))
base.with_suffix(".bib").write_text(to_bibtex(result))
base.with_suffix(".ttl").write_text(to_turtle(result))
```
