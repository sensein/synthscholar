# Agents

The pipeline is powered by 20+ specialised `pydantic-ai` agents, each with a
typed output schema, domain-specific system prompt, and `retries=5`.

## Agent Table

| # | Agent | Output Type | Description |
|---|-------|-------------|-------------|
| 1 | `search_strategy_agent` | `SearchStrategy` | Generates PubMed + bioRxiv queries from PICO |
| 2 | `screening_agent` | `ScreeningBatchResult` | Batched title/abstract + full-text screening |
| 3 | `rob_agent` | `RiskOfBiasResult` | Per-domain risk of bias assessment |
| 4 | `data_extraction_agent` | `StudyDataExtraction` | Structured per-study data extraction |
| 5 | `synthesis_agent` | `str` | Narrative evidence synthesis |
| 6 | `grade_agent` | `GRADEAssessment` | GRADE certainty rating |
| 7 | `bias_summary_agent` | `str` | Overall bias narrative |
| 8 | `limitations_agent` | `str` | Review limitations section |
| 9 | `evidence_extraction_agent` | `BatchEvidenceExtraction` | Per-article evidence spans |
| 10 | `data_charting_agent` | `DataChartingRubric` | 7-section structured charting |
| 11 | `narrative_row_agent` | `PRISMANarrativeRow` | 6-cell condensed study row |
| 12 | `critical_appraisal_agent` | `CriticalAppraisalRubric` | 4-domain quality appraisal |
| 13 | `introduction_section_agent` | `Introduction` | Structured introduction model |
| 14 | `abstract_section_agent` | `Abstract` | Structured abstract model |
| 15 | `thematic_synthesis_agent` | `ThematicSynthesisResult` | Thematic grouping of findings |
| 16 | `discussion_section_agent` | `str` | Discussion prose |
| 17 | `conclusion_section_agent` | `Conclusion` | Structured conclusion model |
| 18 | `quantitative_analysis_agent` | `QuantitativeAnalysis` | Pooled statistics (if data available) |
| 19 | `consensus_synthesis_agent` | `ConsensusSynthesisOutput` | Compare-mode consensus synthesis |
| 20 | `_synthesis_merge_agent` | `MergedSynthesisOutput` | Merges partial syntheses for large reviews |

## Shared Configuration

All agents share:

- **`deps_type=AgentDeps`** — injects `protocol`, `api_key`, `model_name`
- **`retries=5`** — pydantic-ai retries output validation up to 5 times
- **`defer_model_check=True`** — model is resolved at call time, not import time
- **Model** — any OpenRouter model via `OpenAIChatModel` + `OpenRouterProvider`

## `AgentDeps`

```python
@dataclass
class AgentDeps:
    protocol: ReviewProtocol
    api_key: str = ""
    model_name: str = "anthropic/claude-sonnet-4"
    model: object = None    # pre-built model instance (optional)
```

## Using Agents Directly

You can call any agent outside the pipeline:

```python
import asyncio
from synthscholar.agents import (
    AgentDeps, build_model,
    synthesis_agent,
)
from synthscholar import ReviewProtocol

async def main():
    protocol = ReviewProtocol(title="ML in ICU", inclusion_criteria="...")
    deps = AgentDeps(protocol=protocol, api_key="sk-or-...")
    model = build_model(deps.api_key, deps.model_name)

    result = await synthesis_agent.run(
        "Synthesise these findings: ...",
        deps=deps,
        model=model,
    )
    print(result.output)

asyncio.run(main())
```

## Custom Charting & Appraisal Templates

Override the default extraction schema:

```python
from synthscholar import (
    default_charting_template,
    default_appraisal_config,
    ChartingTemplate, ChartingSection, FieldDefinition,
)

# Add a custom charting section
template = default_charting_template()
template.sections.append(
    ChartingSection(
        section_id="H",
        section_name="Cost-effectiveness",
        fields=[
            FieldDefinition(field_id="H1", label="ICER reported", answer_type="yes_no"),
            FieldDefinition(field_id="H2", label="Perspective", answer_type="text"),
        ],
    )
)

result = await pipeline.run(charting_template=template)
```
