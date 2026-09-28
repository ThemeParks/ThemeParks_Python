#!/usr/bin/env python3
"""Moved into the package. This file stays so old links still work.

    pip install themeparks
    themeparks-backfill "Disneyland Park"
    themeparks-backfill "Walt Disney World Resort"    # every park in a destination
    themeparks-backfill --list disney                 # find an id, no key needed

It used to be a script to copy off GitHub. The first paying customer to follow
that link had to work out that the library needed installing, then what the
arguments were, then read a traceback. It is a command now, with `--help` that
answers those questions, so there is nothing here to copy.

The code lives at `themeparks/backfill.py` and is the same logic, tested.
"""

from themeparks.backfill import main

if __name__ == "__main__":
    raise SystemExit(main())
