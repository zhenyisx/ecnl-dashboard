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
- **Playoffs & Finals** — National post-season per age group and competition (Champions League, North American Cup, Showcase Cup, Showcase Games): knockout brackets drawn as trees, cup and consolation brackets, group tables where a group stage exists, round-tagged schedules, and a format note per competition
- **★ My Teams** — Star any team; each favorite opens a summary page: rank, points, record, goals, last-5 form and next game, the full table with the team highlighted, the team's own fixtures and results, and its post-season games when it played any
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

## National playoffs

A season's post-season events live under `national` in `data/sources.json`, keyed
by the stage name shown in the sidebar. 2024-25 had separate `Playoffs` and
`Finals` events; 2025-26 has one combined event, so its row reads
`"Playoffs & Finals"` and the stage selector is hidden.

```json
"national": {
  "Playoffs & Finals": {
    "eventId": 4251,
    "eventName": "ECNL Girls National Playoffs and Finals",
    "location": "Redmond, WA",
    "startDate": "2026-07-11", "endDate": "2026-07-17",
    "tierLabels": { "Friendlies": "Showcase Games" },
    "tierNotes":  { "Champions League": "32 teams seeded by conference PPG …" }
  }
}
```

- `startDate`/`endDate` gate the refresh: the event's flights join the match-day
  refresh from a week before it starts until two weeks after it ends, then drop out.
- `tierLabels` renames TGS flight names for display; `tierNotes` is the collapsible
  "Format" text under each competition, taken from ECNL's post-season structure doc.

**Brackets are derived from the schedule, not from TGS's bracket HTML.** Knockout
flights have no standings, but every game carries both team IDs, scores, PK scores
and a game number. The page follows each team's lineage — losing in round *r* of a
bracket moves it into that round's losers bracket — which reproduces the main
bracket, the Champions League Cup (day-one losers) and the consolation games without
any template. A game that ended level with no PK score recorded is settled from the
next game each team plays. TGS's bracket HTML is archived for durability only.

Not included: the U18/19 National Finals is a separate TGS event (St. Louis, June)
and can be added as a second `national` entry when its event ID is known. The
2024-25 Finals event (3975) publishes no games through the API, so that bracket
ends at the round played at the Playoffs event.

## My Teams

Favorites are stored in the browser (`localStorage`) as records — the team name plus
the IDs that locate it (`eventID`, `divisionID`, `flightID`, `teamID`) — captured from
the standings row when the ★ is clicked. A favorite therefore belongs to one team in
one season; clicking it switches the season selector to that season. Favorites saved
by the earlier version (name only) are located by scanning the archived standings the
first time My Teams is opened, and upgraded in place.

`#tab=teams&season=2026-27&team=<teamID>` deep-links to a team's summary even in a
browser where it isn't a favorite.

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

The site is a Cloudflare Worker serving static assets (`wrangler.toml` at the repo
root: no script, `[assets] directory = "./public"`). It is built by Cloudflare's Git
integration on the **NextOneTwoLabs** Cloudflare account: repository
`NextOneTwoLabs/ecnl-dashboard`, branch `main`, build command empty, deploy command
`npx wrangler deploy`. Pushing to `main` — including the scheduled data commits —
redeploys.

- **Canonical URL:** `https://ecnl-dashboard.nextonetwolabs.workers.dev`
- **Old URL:** `https://ecnl-dashboard.zhenyisx.workers.dev` is a permanent redirect,
  served by the tiny Worker in [`redirect/`](redirect/) from the original personal
  account. Deep-link fragments survive the redirect.

The `workers.dev` subdomain belongs to the Cloudflare account, not to GitHub — moving
the repository between GitHub owners does not change the URL, but Cloudflare's GitHub
App must be installed on the new owner for builds to continue.

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
| Bracket HTML (archived, not rendered) | `Event/get-brackets-design-by-eventID-and-flightID/{eventId}/{flightId}` |
| Event name (for `--verify`) | `Event/get-event-details-by-eventID/{eventId}` |

## Season Coverage

| Season  | Conferences | Division naming | Birth years per group | Playoffs | Finals |
|---------|-------------|-----------------|-----------------------|----------|--------|
| 2026-27 | 10          | age (`GU15`)         | two (`2011/2012`) | —   | —      |
| 2025-26 | 10          | birth year (`G2011`) | one               | ✅ event 4251 (combined Playoffs & Finals, U13–U17) | ↑ same event |
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
