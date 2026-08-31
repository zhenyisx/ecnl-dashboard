# ECNL Girls Conference Standings Dashboard

A standings **and schedule** dashboard for ECNL (Elite Clubs National League) Girls
conferences — a single static HTML page over a self-refreshing local archive of the data.

Everything is mirrored to disk, so the dashboard keeps working from local files if the
upstream API or website ever goes away.

## Features

- **Conference Standings** — All 10 ECNL conferences across 6 seasons (2021-22 through 2026-27), per-flight tables
- **Schedules** — Full fixtures and results in-app: date, kickoff time, venue, both teams, final score. No longer just an outbound link
- **Fully static** — no backend at runtime; deploys to any static host for free, and works offline once loaded
- **Self-refreshing** — a scheduled job updates the data on match days and keeps the fixture calendar current
- **CSV exports** — Human-readable standings and schedule tables under `export/`, openable in Excel
- **Playoffs & Finals** — Group stage standings for the national Playoffs event
- **★ My Teams** — Star any team to track them in a personal favorites list
- **Age group navigation** — Tabs populated from the API; keyboard arrow-key navigation
- **Dark mode**, and **deep links** (season, age group, conference, and view in the URL hash)

## How it works

The site is **fully static**. `archive.py` pre-fetches every API response into
`public/archive/api/**.json`, and the page reads those files directly — it never
calls the API from the browser (which it couldn't anyway: the API sends no CORS
headers). A scheduled GitHub Action keeps the data current.

`public/` is simultaneously the Cloudflare Pages output directory and the local
server root, so **the hosted site and your local copy are the same files** with no
build step between them.

## Run it locally

```bash
cd public && python -m http.server 8000     # any static server works
```

Then open [http://localhost:8000/](http://localhost:8000/). No backend needed.

Or use `python proxy_server.py` (port 5000) if you also want `?live=1`, which
routes data requests through a live API proxy for debugging.

## Refresh the data

```bash
python archive.py --refresh        # what the scheduled workflow runs
```

The refresh is **driven by the fixture calendar**, not a clock. Of 288 days in a
season only 81 have games, and 99.7% of those are Sat/Sun — so on most days there
is provably nothing to fetch:

| Run | Work done |
|---|---|
| Non-match day, not the sweep hour | exits in seconds, **0 requests** |
| Sweep hour (daily) | all flight schedules, rebuilding the calendar |
| Match day | the flights that played, every 2 h |

Standings are only refetched where a **result actually changed** — schedule
payloads carry scores, so a schedule fetch collects results too, and standings
cannot move unless a score did.

Useful flags:

```bash
python archive.py --refresh --sweep                    # force the all-flights sweep
python archive.py --refresh --dry-run --date 2026-09-12  # test a given day
python archive.py --season 2026-27                     # full crawl of one season
python archive.py --all                                # every season (~1,200 requests)
```

Commit `public/archive/` and `export/` — that is what makes the data durable, and
pushing to `main` is what deploys.

## Adding a season or conference

`data/sources.json` is the single source of truth for which event IDs back each
season and conference. Both the dashboard and `archive.py` read it, so nothing is
hardcoded in the HTML.

1. Add the season/conference and its `eventId` to `data/sources.json`. The event ID
   is the number in a TGS URL: `public.totalglobalsports.com/public/event/**3925**/…`
2. Verify it points where you think it does — this calls the API and compares the
   real event name against the `eventName` you recorded:

   ```bash
   python archive.py --verify --season 2026-27
   ```

3. Archive it: `python archive.py --season 2026-27`

The season dropdown rebuilds itself from the registry, so no HTML edit is needed.

## Layout

| Path | What it is |
|---|---|
| **`public/`** | **Everything the site serves** — Pages output dir and local server root |
| `public/index.html` | The whole app — HTML, CSS and JS in one file |
| `public/data/sources.json` | Season → conference → event ID registry, refresh policy, birth-year anchor |
| `public/archive/api/…` | Raw API responses keyed by endpoint path — what the site reads |
| `public/archive/match-days.json` | Fixture calendar that drives the refresh schedule |
| `public/archive/refresh-state.json` | When the data was last refreshed (powers "Updated 3h ago") |
| `public/archive/manifest.json` | Index tying event IDs back to season/conference/flight |
| `archive.py` | Crawler: match-day refresh, bulk backfill, CSV exports, `--verify` |
| `ecnl_api.py` | Shared API/archive helpers |
| `proxy_server.py` | Local static server, plus the `?live=1` API proxy |
| `export/<season>/<conf>/` | CSVs — not published; `*.standings.csv`, `*.schedule.csv` |
| `.github/workflows/refresh.yml` | The 2-hourly scheduled refresh |
| `ecnl-standings.html` | Deprecated first version, kept for reference |

## Deploying

Cloudflare Pages: connect the repo, production branch `main`, **build command:
none**, **build output directory: `public`**. Pushing to `main` deploys.

## Data Sources

- **API**: `https://api.athleteone.com` (TGS / AthleteOne)
- **Public site**: `https://public.totalglobalsports.com` — the original standings and
  schedule pages are still linked from every view, and the URL templates live in
  `data/sources.json` so they can be repointed in one place.

Endpoints used (all unauthenticated):

| Purpose | Path |
|---|---|
| Divisions & flights for an event | `Event/get-event-schedule-or-standings/{eventId}` |
| Standings | `Event/get-standings-by-div-and-flight/{divisionId}/{flightId}/{eventId}` |
| Schedule | `Event/get-schedules-by-flight/{eventId}/{flightId}/0` |
| Bracket HTML | `Event/get-flight-brackets-by-flight/{eventId}/{flightId}` |
| Event name (for `--verify`) | `Event/get-event-details-by-eventID/{eventId}` |

## Season Coverage

| Season  | Conferences | Division naming | Birth years per group | Playoffs | Finals |
|---------|-------------|-----------------|-----------------------|----------|--------|
| 2026-27 | 10          | age (`GU15`)         | two (`2011/2012`) | —   | —      |
| 2025-26 | 10          | birth year (`G2011`) | one               | —   | —      |
| 2024-25 | 10          | birth year | one | ✅ event 3865 | ✅ event 3975 |
| 2023-24 | 10          | birth year | one | —        | —      |
| 2022-23 | 10          | birth year | one | —        | —      |
| 2021-22 | 9 (no NorCal) | age (`GU13`) | one | —      | —      |

Age labels are computed relative to the season being viewed, so historical seasons
stay correctly labelled.

## The birth-year anchor

Division naming is not stable across seasons — some seasons name divisions by birth
year (`G2011`), others by age (`GU15`) — and **from 2026-27 each age group spans two
birth years** (ECNL moved to school-year cohorts). So a single birth year can map to
two age groups: a 2011-born falls in `GU15` or `GU16` in 2026-27 depending on birth month.

The birth-year band is therefore the one anchor that means the same thing in every
season. `archive.py` derives it per division and records it under `ageGroups` in
`data/sources.json`, with the `source` field saying how it was resolved:

| `source` | Meaning | Seasons |
|---|---|---|
| `division-name` | Years read from the division name (`G2008/2007`) | 2022-23 … 2025-26 |
| `team-names` | Years read from the team names (`ECNL G2013/14`) | 2026-27 |
| `computed` | Derived from the U-number and season start year | 2021-22 (names carry no years) |

The dashboard uses this band to order age groups oldest-first consistently in every
season, and shows it on hover over an age-group tab. It refreshes on each
`python archive.py` run; pass `--no-update-sources` to leave the registry untouched.

## Notes

- Team logos are hotlinked from S3, so they will not render with no internet even
  though all standings and schedule data will.
- The page keeps no API cache of its own — `localStorage` holds only your
  preferences (theme, favorites, last view). The on-disk archive replaces the old
  localStorage cache, which was unbounded and silently hit the browser's ~5 MB quota.
- Freshness comes from `public/archive/refresh-state.json`, not file timestamps.
  A git checkout resets every file's mtime, so an mtime-based check would make CI
  believe the archive was just written and skip all work.
- Some games are never scored upstream — 14 from 2025-26 are still blank — so the
  missing-results chase is capped by `refresh.pending.maxPendingAgeDays`.
