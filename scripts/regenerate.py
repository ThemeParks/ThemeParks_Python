"""Regenerate pydantic models from the upstream OpenAPI spec.

Includes a post-generation patch step to relax queue fields that the OpenAPI
spec marks ``nullable: true`` but which ``datamodel-code-generator`` leaves as
required non-Optional. See the plan entry for Task 7 and GitHub issues #1/#2.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SPEC_URL = "https://api.themeparks.wiki/docs/v1.yaml"
OUTPUT = Path(__file__).resolve().parents[1] / "themeparks" / "_generated" / "models.py"

# Rename queue variant classes so they don't collide with LiveQueue field names.
# The upstream OpenAPI spec names the variant schemas STANDBY/SINGLERIDER/etc.,
# which collide with the LiveQueue field names; datamodel-code-generator works
# around this by renaming the LiveQueue.STANDBY attribute to STANDBY_1 with an
# alias, which is a painful UX wart. Renaming the variant classes eliminates
# the collision and lets us keep the attribute name matching the API field.
CLASS_RENAMES: dict[str, str] = {
    "STANDBY": "StandbyQueue",
    "SINGLERIDER": "SingleRiderQueue",
    "RETURNTIME": "ReturnTimeQueue",
    "PAIDRETURNTIME": "PaidReturnTimeQueue",
    "BOARDINGGROUP": "BoardingGroupQueue",
    "PAIDSTANDBY": "PaidStandbyQueue",
}

# (class_name, field_name) pairs for fields the OpenAPI spec marks nullable
# but datamodel-code-generator does not honor when the field is required.
# Keyed on the post-rename class names (see CLASS_RENAMES).
NULLABLE_PATCHES: list[tuple[str, str]] = [
    ("StandbyQueue", "waitTime"),
    ("SingleRiderQueue", "waitTime"),
    ("PaidStandbyQueue", "waitTime"),
    ("ReturnTimeQueue", "state"),
    ("ReturnTimeQueue", "returnStart"),
    ("ReturnTimeQueue", "returnEnd"),
    ("PaidReturnTimeQueue", "state"),
    ("PaidReturnTimeQueue", "returnStart"),
    ("PaidReturnTimeQueue", "returnEnd"),
    ("PaidReturnTimeQueue", "price"),
    ("BoardingGroupQueue", "allocationStatus"),
    ("BoardingGroupQueue", "currentGroupStart"),
    ("BoardingGroupQueue", "currentGroupEnd"),
    ("BoardingGroupQueue", "nextAllocationTime"),
    ("BoardingGroupQueue", "estimatedWait"),
]


def apply_class_renames(text: str) -> str:
    """Rename queue variant classes to avoid colliding with LiveQueue fields."""
    for old, new in CLASS_RENAMES.items():
        text = re.sub(rf"\b{old}\b", new, text)
    return text


def normalize_live_queue(text: str) -> tuple[str, int]:
    """Revert the aliased STANDBY_1 attribute on LiveQueue to a plain STANDBY.

    datamodel-codegen emits:
        STANDBY_1: Annotated[STANDBY | None, Field(alias='STANDBY')] = None
    After :func:`apply_class_renames` runs, the bare ``STANDBY`` class
    reference (and the ``alias='STANDBY'`` string literal) are both rewritten
    to ``StandbyQueue``:
        STANDBY_1: Annotated[StandbyQueue | None, Field(alias='StandbyQueue')] = None
    Since the variant class is no longer named STANDBY, the collision that
    forced the alias is gone and we can restore the natural attribute name.
    This must run *after* :func:`apply_class_renames`.

    Returns ``(patched_text, n_substitutions)`` so the caller can warn loudly
    when zero substitutions occurred (datamodel-codegen output drift).
    """
    return re.subn(
        r"STANDBY_1: Annotated\[StandbyQueue \| None, Field\(alias='StandbyQueue'\)\] = None",
        "STANDBY: StandbyQueue | None = None",
        text,
    )


def apply_nullable_patches(text: str) -> str:
    """Relax the known-nullable queue fields to ``Optional[...] = None``.

    Finds the class block and rewrites a specific field line so that the type
    expression has ``| None = None`` appended, unless it already has it.
    """
    for class_name, field_name in NULLABLE_PATCHES:
        # Match: "class <CLASS>(BaseModel):" then any indented lines until we
        # hit the target field line. Rewrite just that field line.
        pattern = re.compile(
            rf"(?P<head>class {class_name}\(BaseModel\):\n"
            rf"(?:    [^\n]*\n)*?"
            rf"    {field_name}:\s*)"
            rf"(?P<line>[^\n]+)"
            rf"(?P<tail>\n)",
        )

        def repl(m: re.Match[str]) -> str:
            line = m.group("line").rstrip()
            if "None" in line:
                # Already Optional (either in type or via `= None` default).
                return m.group(0)
            return f"{m.group('head')}{line} | None = None{m.group('tail')}"

        new_text, count = pattern.subn(repl, text, count=1)
        if count == 0:
            print(
                f"warning: nullable patch did not match {class_name}.{field_name}",
                file=sys.stderr,
            )
        text = new_text
    return text


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "datamodel_code_generator",
        "--url",
        SPEC_URL,
        "--input-file-type",
        "openapi",
        "--output",
        str(OUTPUT),
        "--output-model-type",
        "pydantic_v2.BaseModel",
        "--use-schema-description",
        "--use-field-description",
        "--use-annotated",
        "--target-python-version",
        "3.10",
        "--disable-timestamp",
    ]
    print("Running:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    print("Applying post-generation patches...")
    original = OUTPUT.read_text()
    # Order matters: apply_class_renames first (which also rewrites the
    # alias='STANDBY' string literal on LiveQueue.STANDBY_1), then
    # normalize_live_queue to collapse the now-redundant alias, then
    # apply_nullable_patches (keyed on the post-rename class names).
    patched = apply_class_renames(original)
    patched, n_normalized = normalize_live_queue(patched)
    if n_normalized == 0:
        print(
            "warning: STANDBY_1 alias normalization did not run - "
            "datamodel-codegen output may have drifted; please inspect models.py",
            file=sys.stderr,
        )
    patched = apply_nullable_patches(patched)
    if patched != original:
        OUTPUT.write_text(patched)
        print(f"  patched {OUTPUT}")
    else:
        print("  no changes (already patched?)")

    # Post-generation sanity check. The running process already imported the
    # stale pre-patch module (if at all), so spawn a subprocess for a fresh
    # import that reflects what downstream users will see.
    print("Verifying post-generation invariants...")
    verify = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from themeparks._generated.models import LiveQueue;"
                "fields = LiveQueue.model_fields;"
                "assert 'STANDBY' in fields, "
                "'post-gen patch failed: STANDBY not found';"
                "assert 'STANDBY_1' not in fields, "
                "'post-gen patch failed: STANDBY_1 leaked'"
            ),
        ],
        check=False,
    )
    if verify.returncode != 0:
        print(
            "error: post-generation invariants failed; see traceback above.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  OK")


if __name__ == "__main__":
    main()
