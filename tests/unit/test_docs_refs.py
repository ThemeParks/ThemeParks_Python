"""Every mkdocstrings reference in docs/ must resolve to a real object.

`mkdocs build --strict` already fails on a dangling reference, but it only
runs on push to main, and it aborts on the first one it meets. So a
regeneration that renames a model merges green and breaks the docs deploy
after the fact — which is exactly what `DestinationParkEntry` -> `Park` and
`Location` -> `EntityLocation` did, for eight consecutive pushes.

This runs in the ordinary unit matrix, costs nothing, and reports every
broken reference at once instead of one per build.
"""

import importlib
import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs"

# ::: themeparks._generated.models.EntityData
REFERENCE = re.compile(r"^:::\s+(themeparks[\w.]*)\s*$")


def _references() -> list[tuple[str, int, str]]:
    """(page relative to docs/, line number, dotted target) for every ::: line."""
    found = []
    for page in sorted(DOCS.rglob("*.md")):
        for lineno, line in enumerate(page.read_text().splitlines(), 1):
            match = REFERENCE.match(line)
            if match:
                found.append((str(page.relative_to(DOCS)), lineno, match.group(1)))
    return found


def _resolves(target: str) -> bool:
    """True if `target` names an importable module or an attribute on one."""
    try:
        importlib.import_module(target)
        return True
    except ImportError:
        pass

    module_path, _, attribute = target.rpartition(".")
    if not module_path:
        return False
    try:
        module = importlib.import_module(module_path)
    except ImportError:
        return False
    return hasattr(module, attribute)


def test_docs_directory_is_present():
    # A wrong DOCS path would make every other test here vacuously pass.
    assert DOCS.is_dir(), DOCS


def test_references_were_found():
    assert _references(), "no mkdocstrings references found — has docs/ moved?"


@pytest.mark.parametrize(("page", "lineno", "target"), _references())
def test_reference_resolves(page: str, lineno: int, target: str):
    assert _resolves(target), f"docs/{page}:{lineno} references missing object {target}"
