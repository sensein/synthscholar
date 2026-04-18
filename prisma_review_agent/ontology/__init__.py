"""SLR Ontology integration — LinkML schema and RDF export (Turtle / JSON-LD)."""

from .rdf_export import to_turtle, to_jsonld

__all__ = ["to_turtle", "to_jsonld"]
