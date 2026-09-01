---
title: Spec drift — models cannot be regenerated from the upstream spec
---

The daily `spec-drift` workflow could not regenerate `themeparks/_generated/models.py`
from `api.themeparks.wiki/docs/v1.yaml`.

The usual cause is the upstream contract renaming or dropping a component
schema that `_ergonomic/` imports, which surfaces as an `ImportError` from
`regenerate.py`'s post-generation invariant check. Fixing it normally means
updating `CLASS_RENAMES` in `scripts/regenerate.py` and the affected imports.

Note `regenerate.py` writes the file before it verifies, so a failed run leaves
`models.py` rewritten on disk. Check out the file before rerunning locally.

This used to fail silently: the regenerate step was fatal, so a breaking
upstream change produced no PR, no smoke tests and no issue. It went unnoticed
for months. Do not make this step fatal again.
