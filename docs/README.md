# Docs — How to Build the Static Site

The documentation is built with [Sphinx](https://www.sphinx-doc.org/) using the
[Furo](https://pradyunsg.me/furo/) theme and [MyST-Parser](https://myst-parser.readthedocs.io/)
so all pages are written in Markdown (not RST).

## Quick Start

Run all commands from the **project root** (the folder containing `docs/`):

```bash
# 1 — install docs dependencies (isolated from the main package)
pip install -r docs/requirements.txt

# 2 — build HTML  (source=docs/  output=docs/_build/html)
sphinx-build -b html docs docs/_build/html

# 3 — open in browser
open docs/_build/html/index.html          # macOS
xdg-open docs/_build/html/index.html     # Linux
start docs/_build/html/index.html        # Windows
```

> **Common error:** `config directory doesn't contain a conf.py file`
> This happens when you run the command from *inside* the `docs/` folder.
> Always run from the project root so that `docs` is a relative path to the source directory.

## Live Preview (auto-reload)

```bash
pip install sphinx-autobuild
# run from project root:
sphinx-autobuild docs docs/_build/html --open-browser
```

The browser refreshes automatically whenever you save a `.md` file.

## Directory Structure

```
docs/
├── conf.py                  # Sphinx configuration (theme, extensions, metadata)
├── requirements.txt         # Docs-only Python dependencies
├── README.md                # This file
│
├── index.md                 # Landing page (hero + feature cards)
├── installation.md          # Installation guide
├── quickstart.md            # Quick start (CLI + Python API)
├── cli.md                   # Full CLI reference
│
├── guides/
│   ├── compare-mode.md      # Multi-model compare mode
│   ├── caching.md           # PostgreSQL caching + checkpoints
│   └── fastapi.md           # FastAPI SSE + plan-confirmation
│
├── api/
│   ├── index.md             # API overview + enums + constants
│   ├── pipeline.md          # PRISMAReviewPipeline reference
│   ├── models.md            # All Pydantic models
│   ├── export.md            # Export functions
│   └── agents.md            # Agent table + direct usage
│
└── _static/
    └── custom.css           # Hero, feature cards, pipeline steps styling
```

## Adding a New Page

1. Create `docs/<section>/my-page.md`
2. Add it to the relevant `toctree` in the parent `index.md` or `docs/index.md`:

   ````markdown
   ```{toctree}
   :maxdepth: 1
   guides/my-page
   ```
   ````

3. Rebuild: `sphinx-build -b html docs docs/_build/html`

## Deploying to GitHub Pages

### Automatic (GitHub Actions)

Create `.github/workflows/docs.yml`:

```yaml
name: Docs

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -r docs/requirements.txt
      - run: sphinx-build -b html docs docs/_build/html
      - uses: actions/upload-pages-artifact@v3
        with:
          path: docs/_build/html

  deploy:
    needs: build
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    steps:
      - uses: actions/deploy-pages@v4
        id: deployment
```

Then enable GitHub Pages in your repo settings:
`Settings → Pages → Source → GitHub Actions`

### Manual

```bash
sphinx-build -b html docs docs/_build/html
gh-pages -d docs/_build/html          # if using gh-pages npm package
```

Or push the `_build/html` contents to the `gh-pages` branch:

```bash
git subtree push --prefix docs/_build/html origin gh-pages
```

## Customising the Theme

All visual customisation is in `docs/_static/custom.css`.

Key CSS classes:

| Class | Used for |
|-------|----------|
| `.hero` | Landing page hero section |
| `.hero-title` | Main headline |
| `.hero-install` | `pip install` code block |
| `.btn-primary` / `.btn-secondary` | CTA buttons |
| `.feature-grid` | Card grid container |
| `.feature-card` | Individual feature card |
| `.pipeline-steps` | Numbered pipeline step list |
| `table.api-table` | Styled API reference tables |

Theme colours are controlled via CSS custom properties in `conf.py`:

```python
html_theme_options = {
    "light_css_variables": {
        "color-brand-primary": "#2563eb",   # blue accent
        ...
    },
}
```

## Dependencies

| Package | Purpose |
|---------|---------|
| `sphinx>=7.2` | Documentation build system |
| `furo>=2024.1.29` | Furo theme (same as NeuroAI site) |
| `myst-parser>=3.0` | Write docs in Markdown instead of RST |
| `sphinx-copybutton>=0.5` | Copy button on code blocks |
