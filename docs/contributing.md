# Contributing

## Regenerating the API models

The `themeparks/_generated/` package is produced from the upstream OpenAPI
spec. To regenerate locally:

```bash
python scripts/regenerate.py
```

CI also watches for spec drift: when the upstream spec changes, CI opens an
automated PR with the regenerated models. Review it like any other PR.

## What every PR must pass

Before a PR can merge, all of these must be green:

```bash
ruff check
ruff format --check
mypy themeparks
pytest tests/unit
```

Run them locally before pushing to avoid a round-trip through CI.

## Documentation

The site you're reading is built with [MkDocs Material](https://squidfunk.github.io/mkdocs-material/)
and [mkdocstrings](https://mkdocstrings.github.io/). To preview locally:

```bash
pip install -e '.[docs]'
mkdocs serve
```

Then open <http://127.0.0.1:8000>.

Verify a production build with:

```bash
mkdocs build --strict
```

`--strict` treats warnings (broken links, missing references) as errors.

### GitHub Pages setup

The `.github/workflows/docs.yml` workflow deploys to GitHub Pages on every
push to `main` and on every `v*` tag. **A repo admin must enable Pages once**
in repository settings: *Settings → Pages → Source: GitHub Actions*. CI
can't flip that switch on your behalf.
