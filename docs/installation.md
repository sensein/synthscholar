# Installation

## Requirements

- Python **3.11+**
- An [OpenRouter](https://openrouter.ai/) API key (set as `OPENROUTER_API_KEY`)
- PostgreSQL 15+ *(optional — required only for caching and checkpoints)*

## From PyPI

```bash
pip install synthscholar
```

## From Source

```bash
git clone https://github.com/tekrajchhetri/synthscholar
cd synthscholar
pip install -e .
```

Using [`uv`](https://github.com/astral-sh/uv) (recommended):

```bash
uv sync
```

## API Key

The agent uses OpenRouter to access LLMs. Set your key before running:

```bash
export OPENROUTER_API_KEY="sk-or-..."
```

Or pass it at runtime:

```bash
prisma-review --api-key sk-or-... --title "..."
```

## PostgreSQL Setup *(optional)*

For caching and large-review checkpointing, provide a PostgreSQL DSN.
Migrations are applied automatically on first run:

```bash
prisma-review --pg-dsn "postgresql://user:pass@localhost/prismadb" --title "..."
```

The three migrations applied automatically:

| Migration | Description |
|-----------|-------------|
| `001_initial.sql` | `review_cache` table for result caching |
| `002_add_article_store.sql` | `article_store` with GIN/tsvector full-text search |
| `003_add_pipeline_checkpoints.sql` | `pipeline_checkpoints` for batch resumability |

## Verifying Installation

```bash
prisma-review --help
```

```python
import prisma_review_agent
print(prisma_review_agent.__version__)
```
