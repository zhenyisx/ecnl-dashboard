# ECNL Girls Conference Standings Dashboard

A standings **and schedule** dashboard for ECNL (Elite Clubs National League) Girls conferences,
built as a single-page HTML app backed by a lightweight Python proxy server.

Everything it fetches is mirrored to disk, so the dashboard keeps working from local
files if the upstream API or website ever goes away.

## Features

- **Conference Standings** — All 10 ECNL conferences across 5 seasons (2021-22 through 2025-26), per-flight tables
- **Schedules** — Full fixtures and results in-app: date, kickoff time, venue, both teams, final score. No longer just an outbound link
- **Offline archive** — Every API response is saved under `archive/`; if the live API is unreachable the proxy serves the archived copy automatically, and the page shows an **Archived** badge
- **CSV exports** — Human-readable standings and schedule tables under `export/`, openable in Excel
- **Playoffs & Finals** — Group stage standings for the national Playoffs event
- **★ My Teams** — Star any team to track them in a personal favorites list
- **Age group navigation** — Tabs populated from the API; keyboard arrow-key navigation
- **Dark mode**, and **deep links** (season, age group, conference, and view in the URL hash)

## Setup

### 1. Start the server

```bash
python proxy_server.py
```

Serves on **port 5000**, proxying API calls to `https://api.athleteone.com` and
archiving every response it receives.

### 2. Open the dashboard

[http://localhost:5000/ecnl-dashboard.html](http://localhost:5000/ecnl-dashboard.html)

### 3. Build the offline archive

Browsing archives whatever you look at. To capture everything in one pass:

```bash
python archive.py                      # newest season
python archive.py --season 2024-25
python archive.py --all                # every season (~1,200 requests, ~10 min)
```

Then confirm the archive is complete by running with no network access at all:

```bash
python proxy_server.py --offline
```

Commit `archive/` and `export/` to git — that is what makes the data durable.

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
| `ecnl-dashboard.html` | The whole app — HTML, CSS and JS in one file |
| `data/sources.json` | Season → conference → event ID registry, with provenance |
| `proxy_server.py` | Static server + API proxy + write-through archive + fallback |
| `archive.py` | Bulk crawler: archive mirror, CSV exports, manifest |
| `ecnl_api.py` | Shared API/archive helpers used by both scripts |
| `archive/api/…` | Raw API responses, keyed by endpoint path (what offline mode replays) |
| `archive/manifest.json` | Index tying event IDs back to season/conference/flight |
| `export/<season>/<conf>/` | `*.standings.csv`, `*.schedule.csv`, `_all.standings.csv` |
| `ecnl-standings.html` | Deprecated first version, kept for reference |

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
