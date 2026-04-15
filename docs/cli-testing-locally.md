# Testing locally

## Install from the v2 branch

```bash
pip install "git+https://github.com/ThemeParks/ThemeParks_Python.git@v2"
```

Or for a local editable checkout with the full dev toolchain:

```bash
git clone -b v2 https://github.com/ThemeParks/ThemeParks_Python.git
cd ThemeParks_Python
pip install -e '.[dev]'
```

## Run the unit tests

The unit tests are fully offline — they use recorded fixtures. They run in
under a second:

```bash
pytest tests/unit
```

## Run the live smoke tests

The live tests hit the real ThemeParks.wiki API. Use them to sanity-check
that nothing regressed against production:

```bash
pytest tests/live
```

Live tests are rate-limit aware but intentionally hit the network — don't run
them in a tight loop.

## Lint and type-check

```bash
ruff check
ruff format --check
mypy themeparks
```

All four (lint, format, type-check, unit tests) must pass before a PR can
land.
