# Fixtures captured from the live API

Not hand-written. Each file here was taken from a real response, and the header
below says which endpoint and when. That matters because of a specific failure
this project keeps repeating.

## Why this directory exists

On 2026-09-28 a 403-recovery fix shipped doing nothing. Its tests passed because
the fixture was built from the *formatted text of a traceback* rather than from a
response body, so the code and the test were wrong together and agreed with each
other. The memory note for that pattern is `self-confirming-harness`, and it was
its fifth occurrence in one day.

A fixture the author of the code also invented proves only that the two are
consistent. A fixture taken from the real thing can disagree.

## destinations_slice.json

`GET https://api.themeparks.wiki/v1/destinations`, captured 2026-09-28, trimmed
to 9 destinations and 20 parks and otherwise verbatim — every id, name and slug
is exactly what the API returned.

It is trimmed to keep, deliberately, every case that broke name resolution:

| Case | Why it is here |
|---|---|
| `Walt Disney World® Resort` | U+00AE. The command's own documented example, `"Walt Disney World Resort"`, did not match it. |
| `LEGOLAND® Korea` | the same, on a destination whose park shares the name |
| `Walibi Rhône-Alpes` | U+00F4, so a normaliser must decompose accents |
| `Knott's Berry Farm` + `Knott’s Soak City` | ASCII `'` and U+2019 **in one destination**. Nobody types the curly one. |
| `Disneyland Park` ×2 | Anaheim and Paris, identical park names. The reason the candidate list names the destination. |
| `Hurricane Harbor` + `Hurricane Harbor Chicago` | an exact match with a substring rival: the shape that silently downloaded the wrong park |
| `Cedar Point` | destination name == park name, with a second park, so "exact destination beats exact park" is observable |

**Do not edit these by hand.** Re-capture them. If a name upstream has drifted,
that is a real change and the test should notice.
