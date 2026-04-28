---
myst:
  html_meta:
    description: "SynthScholar — AI-powered systematic literature review automation"
---

# SynthScholar

```{raw} html
<div class="hero">
  <h1 class="hero-title">Automated. Rigorous.<br>PRISMA-Guided.</h1>
  <p class="hero-tagline">
    AI-powered PRISMA-guided systematic multi-agent systems literature review.
  </p>
  <div class="hero-install">pip install synthscholar</div>
  <div class="hero-cta">
    <a class="btn-primary" href="quickstart.html">Get Started</a>
    <a class="btn-secondary" href="api/index.html">API Reference</a>
  </div>
</div>

<div class="feature-grid">
  <a class="feature-card" href="quickstart.html">
    <span class="card-icon">⚡</span>
    <div class="card-title">Quick Start</div>
    <div class="card-desc">Run a full systematic review in one CLI command or a few lines of Python.</div>
  </a>
  <a class="feature-card" href="guides/compare-mode.html">
    <span class="card-icon">⚖️</span>
    <div class="card-title">Compare Mode</div>
    <div class="card-desc">Run 2+ LLMs in parallel and measure field-level agreement across models.</div>
  </a>
  <a class="feature-card" href="guides/caching.html">
    <span class="card-icon">🗄️</span>
    <div class="card-title">PostgreSQL Cache</div>
    <div class="card-desc">Cache reviews by protocol similarity and resume large reviews with checkpoints.</div>
  </a>
  <a class="feature-card" href="architecture.html">
    <span class="card-icon">🔬</span>
    <div class="card-title">Architecture</div>
    <div class="card-desc">18-step async pipeline, agent topology, data flow, and storage schema.</div>
  </a>
  <a class="feature-card" href="ontology.html">
    <span class="card-icon">🔗</span>
    <div class="card-title">SLR Ontology</div>
    <div class="card-desc">LinkML schema reusing PROV-O, FaBiO, BIBO, OA. Export to Turtle / JSON-LD / SPARQL.</div>
  </a>
  <a class="feature-card" href="guides/fastapi.html">
    <span class="card-icon">🌐</span>
    <div class="card-title">FastAPI Integration</div>
    <div class="card-desc">SSE progress streaming and plan-confirmation callbacks for web applications.</div>
  </a>
</div>
```

## What is SynthScholar?

**SynthScholar** is a multi-agent system for systematic literature review. It follows 
[PRISMA 2020](https://www.prisma-statement.org/) guidelines for systematic literature. It provides [risk of bias (ROB) assessment](https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-08), data charting, critical
appraisal, narrative synthesis, and GRADE rating — producing structured outputs, which can be exported into different format: (i) markdown; (ii) JSON; and (iii) Turtle and JSON-LD. 


## Highlights
 
- **Multi-model compare mode** — run parallel literature reviews across multiple LLMs, producing separate analyses for side-by-side comparison.
- **PostgreSQL caching** — skip repeated LLM calls for similar protocols (≥ 95% match). Can be forced to run fresh reviews.
- **Human-in-the-loop**  — provides human-in-loop features for revising and approving the auto generated search strategy.
- **Source grounding validation** — every evidence span fuzzy-matched back to its source.
- **Data charting and critical appraisal** - supports the data charting and critical apprisal synthesis.
- **Configurable RoB tools** — RoB 2, ROBINS-I, Newcastle-Ottawa, QUADAS-2.
- **Multiple format** export — Turtle + JSON-LD using the SLR Ontology, JSON and Markdown format.
- **API Integration Ready** — Provides features such as SSE streaming so that it can be integrated to FastAPI/UI.

---

```{toctree}
:maxdepth: 1
:caption: Getting Started

installation
quickstart
architecture
ontology
cli
```

```{toctree}
:maxdepth: 1
:caption: Guides

guides/compare-mode
guides/ui-integration
guides/caching
guides/fastapi
```

```{toctree}
:maxdepth: 1
:caption: API Reference

api/index
```

```{toctree}
:maxdepth: 1
:caption: Known Limitations

limitations/index
```