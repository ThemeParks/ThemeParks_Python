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

# (class_name, field_name) pairs for fields the OpenAPI spec marks nullable
# but datamodel-code-generator does not honor when the field is required.
NULLABLE_PATCHES: list[tuple[str, str]] = [
    ("STANDBY", "waitTime"),
    ("SINGLERIDER", "waitTime"),
    ("PAIDSTANDBY", "waitTime"),
    ("RETURNTIME", "state"),
    ("RETURNTIME", "returnStart"),
    ("RETURNTIME", "returnEnd"),
    ("PAIDRETURNTIME", "state"),
    ("PAIDRETURNTIME", "returnStart"),
    ("PAIDRETURNTIME", "returnEnd"),
    ("PAIDRETURNTIME", "price"),
    ("BOARDINGGROUP", "allocationStatus"),
    ("BOARDINGGROUP", "currentGroupStart"),
    ("BOARDINGGROUP", "currentGroupEnd"),
    ("BOARDINGGROUP", "nextAllocationTime"),
    ("BOARDINGGROUP", "estimatedWait"),
]


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

    print("Applying post-generation nullable patches...")
    original = OUTPUT.read_text()
    patched = apply_nullable_patches(original)
    if patched != original:
        OUTPUT.write_text(patched)
        print(f"  patched {OUTPUT}")
    else:
        print("  no changes (already patched?)")


if __name__ == "__main__":
    main()
