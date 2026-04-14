"""Regenerate pydantic models from the upstream OpenAPI spec."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SPEC_URL = "https://api.themeparks.wiki/docs/v1.yaml"
OUTPUT = Path(__file__).resolve().parents[1] / "themeparks" / "_generated" / "models.py"


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


if __name__ == "__main__":
    main()
