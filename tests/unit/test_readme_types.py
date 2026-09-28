"""The README's queue table states field types; they must be the models' types.

It said `waitTime: int | None` while the generated models, and the spec they
come from, say a JSON `number`, which is a `float` here. A customer who believed
the README wrote `f"{wait:d}"` and got a ValueError, or dumped raw history rows
and found `6.0` where they expected `6`. The spec is the authority; the README
follows it, and this test is what keeps it following.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union, get_args, get_type_hints

from themeparks._generated import models

README = Path(__file__).resolve().parents[2] / "README.md"

# | `queue.STANDBY` | `StandbyQueue` | `waitTime: float \\| None` |
ROW = re.compile(r"^\|\s*`queue\.\w+`\s*\|\s*`(\w+)`\s*\|\s*`(\w+): ([^`]+)`\s*\|\s*$")

_NAMES = {"int": int, "float": float, "str": str, "bool": bool, "None": type(None)}


def _declared() -> list[tuple[str, str, str]]:
    return [
        (m.group(1), m.group(2), m.group(3).replace("\\|", "|"))
        for line in README.read_text(encoding="utf-8").splitlines()
        if (m := ROW.match(line))
    ]


def _parse(text: str) -> set[type]:
    return {_NAMES[part.strip()] for part in text.split("|")}


def test_the_table_was_found() -> None:
    # A reworded table would make the test below vacuously pass.
    assert len(_declared()) == 3


def test_every_stated_type_is_the_model_type() -> None:
    for model_name, field, stated in _declared():
        hint = get_type_hints(getattr(models, model_name))[field]
        actual = set(get_args(hint)) if get_args(hint) else {hint}
        assert _parse(stated) == actual, f"README says {model_name}.{field}: {stated}"


def test_the_model_type_is_what_the_spec_says() -> None:
    # `number` in the spec. If the generator ever maps it to int, the README
    # line above has to change with it, and this says why it failed.
    hint = get_type_hints(models.StandbyQueue)["waitTime"]
    assert hint == Union[float, None]
