# API Reference

Full reference for all public classes, functions, and exceptions in
`prisma_review_agent`.

```{toctree}
:maxdepth: 1

pipeline
models
export
agents
```

## Package Overview

```python
import prisma_review_agent as pra

# Core
pra.PRISMAReviewPipeline
pra.ReviewProtocol

# Results
pra.PRISMAReviewResult
pra.CompareReviewResult

# Export
pra.to_markdown, pra.to_json, pra.to_bibtex
pra.to_turtle, pra.to_jsonld

# Exceptions
pra.PlanRejectedError
pra.MaxIterationsReachedError
pra.BatchMaxRetriesError
```

## Enumerations

| Enum | Values |
|------|--------|
| `RoBTool` | `RoB 2`, `ROBINS-I`, `ROBINS-E`, `Newcastle-Ottawa Scale`, `QUADAS-2`, `CASP Qualitative Checklist`, `JBI Critical Appraisal`, `Murad Tool`, `Jadad Scale` |
| `RoBJudgment` | `LOW`, `SOME`, `HIGH` |
| `GRADECertainty` | `HIGH`, `MODERATE`, `LOW`, `VERY_LOW` |
| `InclusionStatus` | `INCLUDE`, `EXCLUDE` |
| `GroundingVerdict` | `GROUNDED`, `PARTIALLY_GROUNDED`, `UNGROUNDED` |

## Pipeline Stage Constants

```python
from prisma_review_agent import (
    STAGE_TITLE_ABSTRACT,   # "Title/Abstract screening"
    STAGE_FULL_TEXT,        # "Full-text screening"
    STAGE_EXTRACTION,       # "Evidence extraction"
    STAGE_CHARTING,         # "Data charting"
    STAGE_ROB,              # "Risk of bias assessment"
    STAGE_APPRAISAL,        # "Critical appraisal"
    STAGE_NARRATIVE,        # "Narrative synthesis"
    STAGE_SYNTHESIS,        # "Synthesis"
    STAGE_SYNTHESIS_MERGE,  # "Synthesis merge"
    STAGE_ASSEMBLY,         # "Assembly"
)
```
