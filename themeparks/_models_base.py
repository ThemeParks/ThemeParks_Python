"""The base class every generated model inherits.

Its whole job is one line of configuration: keep fields the schema does not
declare.

Pydantic's default is to DROP them. The vendored spec lagged the API by a few
days, and in that window `unknownMinutes`, `inParkHours` and `extremeWaits` were
deleted on the way in -- not merely untyped, gone, for every caller of `days()`.
Nothing failed and nothing warned; the data simply was not there. The TypeScript
SDK never had this problem because its types are erased at runtime, so the same
stale schema cost it nothing but autocompletion.

With `extra="allow"` a field the API adds tomorrow survives parsing, reaches
`model_dump()`, and reaches anyone writing rows to a file, before this SDK knows
it exists. It stops being a deadline.

Reading an extra field in typed code still needs a regeneration, which is what the
nightly spec-drift job is for. This is about not losing it in the meantime.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """A response object: validated on what we know, lossless on what we do not."""

    model_config = ConfigDict(extra="allow")
