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

## mk_park_daily_page1.json / mk_park_daily_page2.json

Two consecutive pages of one real request, captured 2026-09-28:
`GET /entity/75ea578a-adc8-4116-a54d-dccb60765ef9/history/daily?from=2026-08-01&to=2026-09-20`
then its `next` followed verbatim. Trimmed to three entities (an attraction, a
show, a restaurant); `range`, `next` and every row are the server's.

They are the oracle for resumable paging. Page one covers through 2026-08-31 and
the server says carry on at 2026-09-01, but two of its three entities have no
rows after 2026-08-30 -- so a checkpoint taken from the newest ROW rewinds and
re-downloads days already written. The same files are in the JavaScript SDK, so
both ports are tested against identical bytes.

## space_mountain_history_2026-09-26.json / space_mountain_daily_2026-09-26.json

`GET /entity/b2260923-9315-40fd-9c6b-44dd811dbe64/history?date=2026-09-26` and
`GET /entity/b2260923-9315-40fd-9c6b-44dd811dbe64/history/daily?date=2026-09-26`,
both captured 2026-09-28 without a key, verbatim.

They are the oracle for `changes().opening`. Space Mountain's opening that day is
`OPERATING`, because the previous night's hours ran past midnight, and its first
row is the close at 00:01:03 local. A day rebuilt from the rows alone has 63
seconds with no known status; rebuilt from the opening plus the rows it has
none, and its first open and last close are the daily row's `firstOperatingAt`
and `lastClosedAt`. If a re-capture picks a day whose opening is `CLOSED`, the
tests stop being able to tell the two apart, and one of them says so.
