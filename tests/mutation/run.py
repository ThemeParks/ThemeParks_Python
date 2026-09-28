"""Apply each committed mutant, run the suite, and report the survivors.

A survivor is a change to production code that breaks no test: either the tests
are blind to it, or the code does not matter. Both are worth knowing and neither
is worth blocking a commit over, so this is a nightly job rather than a gate --
it re-runs the whole suite once per mutant, which is minutes, not seconds.

WHY THE LIST IS COMMITTED rather than generated: a list written by the author of
the tests contains the mutations those tests already catch. On 2026-09-28 an
author-written set scored 14/14 on this package while an independent 54-mutant
sweep found 27 survivors, one of which was the defect that release existed to
fix. A reviewable list is the part that makes the score mean anything.

Usage:
    python tests/mutation/run.py            # every mutant
    python tests/mutation/run.py --list     # names only, runs nothing
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MUTANTS = Path(__file__).with_name("mutants.json")


def run_suite() -> bool:
    """True when the suite passes. Quiet: only the verdict matters here."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "tests/unit"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print the mutants and exit")
    args = parser.parse_args()

    mutants = json.loads(MUTANTS.read_text(encoding="utf-8"))["mutants"]
    if args.list:
        for m in mutants:
            print(f"{m['name']}\n    {m['file']}: {m['why']}")
        return 0

    # A mutant whose `find` no longer matches is NOT a pass. The code moved and
    # nobody updated the mutant, so it has been silently testing nothing -- which
    # is the same failure mode as a test that cannot fail.
    stale: list[str] = []
    survived: list[dict[str, str]] = []
    killed = 0

    for mutant in mutants:
        path = ROOT / mutant["file"]
        original = path.read_text(encoding="utf-8")
        if mutant["find"] not in original:
            stale.append(mutant["name"])
            print(f"STALE     {mutant['name']}", flush=True)
            continue
        path.write_text(original.replace(mutant["find"], mutant["replace"], 1), encoding="utf-8")
        try:
            passed = run_suite()
        finally:
            # Restored whatever happened, including a KeyboardInterrupt: leaving a
            # mutated working tree behind is worse than any result.
            path.write_text(original, encoding="utf-8")
        if passed:
            survived.append(mutant)
            print(f"SURVIVED  {mutant['name']}", flush=True)
        else:
            killed += 1
            print(f"killed    {mutant['name']}", flush=True)

    total = len(mutants)
    print(f"\n{killed}/{total} killed, {len(survived)} survived, {len(stale)} stale")
    for m in survived:
        print(f"\nSURVIVED: {m['name']}\n  {m['file']}\n  {m['why']}")
    for name in stale:
        print(f"\nSTALE: {name}\n  its `find` no longer matches; the mutant is testing nothing")

    return 1 if (survived or stale) else 0


if __name__ == "__main__":
    raise SystemExit(main())
