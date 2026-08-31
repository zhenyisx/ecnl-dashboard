"""
ECNL Dashboard archiver (zero-dependency).

Crawls the events listed in public/data/sources.json and writes:
  public/archive/api/<endpoint-path>.json   raw API mirror (what the site serves)
  public/archive/match-days.json            fixture calendar driving the refresh
  public/archive/refresh-state.json         when data was last refreshed
  export/<season>/<conference>/*.csv        human-readable standings & schedules
  public/archive/manifest.json              index tying event IDs to season/conference

Scheduled use (what the GitHub workflow runs every 2h):
    python archive.py --refresh               match-day driven; no-ops on quiet days
    python archive.py --refresh --sweep       force the all-flights schedule sweep
    python archive.py --refresh --dry-run --date 2026-09-12    test a given day

Manual/bulk use:
    python archive.py --verify --all          check every event ID resolves
    python archive.py --season 2026-27        full crawl of one season
    python archive.py --all                   every season (~1200+ requests)
    python archive.py --all --force           ignore the freshness check
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time

import ecnl_api as api

# Skip re-fetching anything archived more recently than this, unless --force.
FRESH_SECONDS = 12 * 3600
# Politeness delay between API calls.
DELAY = 0.25


class Stats:
    def __init__(self):
        self.fetched = 0
        self.skipped = 0
        self.failed = 0
        self.errors = []

    def fail(self, msg):
        self.failed += 1
        self.errors.append(msg)


def get_json(path, stats, force):
    """Fetch an API path, archive the raw bytes, return parsed JSON.

    Uses the archived copy when it is fresh and --force was not passed.
    """
    age = api.archive_age_seconds(path)
    if not force and age is not None and age < FRESH_SECONDS:
        raw, _ = api.read_archive(path)
        if raw:
            stats.skipped += 1
            return json.loads(raw)

    raw = api.fetch_api_raw(path)
    data = json.loads(raw)  # validate before writing
    api.write_archive(path, raw)
    stats.fetched += 1
    time.sleep(DELAY)
    return data


# ---------- CSV export ----------

STANDINGS_COLUMNS = [
    "rank", "team", "club", "teamID", "gp", "w", "l", "d",
    "pts", "ppg", "gf", "ga", "gd",
]

SCHEDULE_COLUMNS = [
    "date", "time", "type", "homeTeam", "homeScore", "awayScore", "awayTeam",
    "complex", "venue", "status", "matchID",
]


def standings_rows(teams):
    rows = []
    for i, t in enumerate(teams):
        rows.append({
            "rank": i + 1,
            "team": t.get("name"),
            "club": t.get("clubName"),
            "teamID": t.get("teamID"),
            "gp": t.get("gp"),
            "w": t.get("wins"),
            "l": t.get("losses"),
            "d": t.get("draws"),
            "pts": t.get("standingpoints"),
            "ppg": t.get("ppg"),
            "gf": t.get("goalsfor"),
            "ga": t.get("goalsagainst"),
            "gd": t.get("goaldifferential"),
        })
    return rows


def schedule_rows(games):
    rows = []
    for g in sorted(games, key=lambda x: (x.get("gameDate") or "", x.get("gameTime") or "")):
        date = (g.get("gameDate") or "")[:10]
        rows.append({
            "date": date,
            "time": g.get("gameTimeText"),
            "type": g.get("type"),
            "homeTeam": g.get("homeTeam"),
            "homeScore": g.get("hometeamscore"),
            "awayScore": g.get("awayteamscore"),
            "awayTeam": g.get("awayTeam"),
            "complex": g.get("complex"),
            "venue": g.get("venue"),
            "status": g.get("status"),
            "matchID": g.get("matchID"),
        })
    return rows


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


# ---------- verification ----------

def verify(sources, season, conference):
    """Confirm each configured event ID resolves to the expected event name."""
    ok = bad = 0
    for season_key, kind, name, event in api.iter_events(sources, season, conference):
        eid = event.get("eventId")
        if not eid:
            print(f"  SKIP  {season_key} {name}: no eventId set")
            continue
        try:
            data = api.unwrap(api.fetch_api(api.p_event_details(eid)))
            actual = (data or {}).get("name")
        except api.ApiError as e:
            print(f"  FAIL  {season_key} {name} ({eid}): {e}")
            bad += 1
            continue
        expected = event.get("eventName")
        if expected and actual != expected:
            print(f"  DIFF  {season_key} {name} ({eid})")
            print(f"        registry: {expected!r}")
            print(f"        live:     {actual!r}")
            bad += 1
        else:
            print(f"  OK    {season_key} {name} ({eid}) {actual!r}")
            ok += 1
        time.sleep(DELAY)
    print(f"\n{ok} verified, {bad} problem(s).")
    return 1 if bad else 0


# ---------- archiving ----------

def archive_event(sources, season_key, kind, name, event, stats, force, dry_run):
    eid = event.get("eventId")
    if not eid:
        print(f"  skip {season_key} / {name}: no eventId")
        return None

    label = f"{season_key} / {name} ({eid})"
    if dry_run:
        print(f"  would archive {label}")
        return None
    print(f"  {label}")

    try:
        hierarchy = api.unwrap(get_json(api.p_hierarchy(eid), stats, force))
    except api.ApiError as e:
        stats.fail(f"{label}: hierarchy: {e}")
        print(f"    ! hierarchy failed: {e}")
        return None

    divisions = (hierarchy or {}).get("girlsDivAndFlightList") or []
    templates = sources.get("publicUrlTemplates", {})
    export_base = os.path.join(api.EXPORT_DIR, api.slug(season_key), api.slug(name))

    entry = {
        "season": season_key,
        "kind": kind,
        "name": name,
        "eventId": eid,
        "eventName": event.get("eventName"),
        "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "publicUrl": templates.get("eventHome", "").replace("{eventId}", str(eid)),
        "divisions": [],
    }

    all_standings = []
    div_team_names = {}  # divisionName -> [team names], for the birth-year anchor

    for div in divisions:
        div_id = div.get("divisionID")
        div_name = div.get("divisionName")
        for flight in (div.get("flightList") or []):
            flight_id = flight.get("flightID")
            flight_name = flight.get("flightName")
            stem = f"{api.slug(div_name)}-{api.slug(flight_name)}"
            record = {
                "divisionID": div_id,
                "divisionName": div_name,
                "flightID": flight_id,
                "flightName": flight_name,
                "standingsUrl": templates.get("standings", "")
                    .replace("{eventId}", str(eid)).replace("{flightId}", str(flight_id)),
                "schedulesUrl": templates.get("schedules", "")
                    .replace("{eventId}", str(eid)).replace("{flightId}", str(flight_id)),
            }

            # Standings
            try:
                payload = api.unwrap(get_json(api.p_standings(div_id, flight_id, eid), stats, force))
                block = payload[0] if isinstance(payload, list) and payload else (payload or {})
                teams = block.get("teamStandings") or []
                record["teams"] = len(teams)
                div_team_names.setdefault(div_name, []).extend(
                    t.get("name") or "" for t in teams)
                rows = standings_rows(teams)
                if rows:
                    write_csv(os.path.join(export_base, stem + ".standings.csv"),
                              STANDINGS_COLUMNS, rows)
                    for r in rows:
                        all_standings.append(dict(r, division=div_name, flight=flight_name))
            except api.ApiError as e:
                stats.fail(f"{label} {div_name}/{flight_name}: standings: {e}")
                print(f"    ! standings {div_name}/{flight_name}: {e}")

            # Schedule
            try:
                games = api.unwrap(get_json(api.p_schedule(eid, flight_id), stats, force)) or []
                record["games"] = len(games)
                rows = schedule_rows(games)
                if rows:
                    write_csv(os.path.join(export_base, stem + ".schedule.csv"),
                              SCHEDULE_COLUMNS, rows)
            except api.ApiError as e:
                stats.fail(f"{label} {div_name}/{flight_name}: schedule: {e}")
                print(f"    ! schedule {div_name}/{flight_name}: {e}")

            # Brackets, for national playoff/finals events only
            if kind == "national":
                try:
                    get_json(api.p_brackets(eid, flight_id), stats, force)
                    record["brackets"] = True
                except api.ApiError as e:
                    print(f"    - no brackets for {div_name}/{flight_name}: {e}")

            entry["divisions"].append(record)

    if all_standings:
        write_csv(os.path.join(export_base, "_all.standings.csv"),
                  STANDINGS_COLUMNS + ["division", "flight"], all_standings)

    entry["_divTeamNames"] = div_team_names  # consumed by update_age_groups, not persisted
    return entry


# ---------- birth-year anchor ----------

def update_age_groups(sources, season_key, divisions, div_team_names):
    """Derive each division's birth-year band and record it under the season.

    Returns a list of human-readable change descriptions.
    """
    season = sources["seasons"][season_key]
    start_year = season.get("startYear") or int(season_key[:4])
    existing = season.get("ageGroups") or {}

    # Two-year bands (school-year cohorts) began in 2026-27. This only matters
    # as a fallback when neither the division nor the team names carry years.
    two_year = start_year >= 2026

    changes = []
    for div in divisions:
        name = div.get("divisionName")
        if not name:
            continue
        resolved = api.resolve_age_group(
            name, start_year, div_team_names.get(name, []), two_year_bands=two_year)
        if not resolved:
            continue
        prev = existing.get(name)
        # Never downgrade a band we already resolved from real data to a computed guess
        if prev and prev.get("source") != "computed" and resolved["source"] == "computed":
            continue
        if prev != resolved:
            changes.append(
                f"{season_key} {name}: {prev['birthYears'] if prev else '—'} -> "
                f"{resolved['birthYears']} (U{resolved['u']}, {resolved['source']})")
        existing[name] = resolved

    if existing:
        # Oldest cohort first, by earliest birth year — the cross-season anchor
        season["ageGroups"] = dict(
            sorted(existing.items(), key=lambda kv: kv[1]["birthYears"][0]))
    return changes


def save_sources(sources):
    """Rewrite data/sources.json, preserving key order. Reformats to 2-space indent."""
    tmp = api.SOURCES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, api.SOURCES_PATH)


# ---------- match-day driven refresh ----------
#
# Schedules carry scores as well as fixtures, so one schedule fetch collects
# both. Standings is the only endpoint needing a separate call, and it can only
# move if a score moved — so it is gated behind a diff.

def fetch_json(path, stats):
    """Fetch and archive, ignoring the mtime freshness check.

    Refresh mode must never trust mtime: a CI checkout resets every file's mtime
    to clone time, which would make everything look fresh and silently skip all
    work. refresh-state.json is the authority instead.
    """
    raw = api.fetch_api_raw(path)
    data = json.loads(raw)  # validate before writing
    api.write_archive(path, raw)
    stats.fetched += 1
    time.sleep(DELAY)
    return data


def season_flights(sources, season):
    """Every flight in a season, from the archived hierarchies.

    Yields dicts with the conference, event, division and flight identifiers.
    """
    out = []
    for conf, ev in (sources["seasons"][season].get("conferences") or {}).items():
        eid = ev.get("eventId")
        if not eid:
            continue
        raw, _ = api.read_archive(api.p_hierarchy(eid))
        if not raw:
            continue
        try:
            divs = json.loads(raw)["data"]["girlsDivAndFlightList"] or []
        except (ValueError, KeyError, TypeError):
            continue
        for d in divs:
            for f in d.get("flightList") or []:
                out.append({
                    "conference": conf,
                    "eventId": eid,
                    "divisionID": d.get("divisionID"),
                    "divisionName": d.get("divisionName"),
                    "flightID": f.get("flightID"),
                    "flightName": f.get("flightName"),
                    "key": api.flight_key(eid, f.get("flightID")),
                })
    return out


def archived_games(event_id, flight_id):
    raw, _ = api.read_archive(api.p_schedule(event_id, flight_id))
    if not raw:
        return []
    try:
        return json.loads(raw).get("data") or []
    except ValueError:
        return []


def build_match_days(sources, season, flights):
    """Rebuild the fixture calendar from the archived schedules."""
    days = {}
    for fl in flights:
        for g in archived_games(fl["eventId"], fl["flightID"]):
            d = api.game_date(g)
            if not d:
                continue
            entry = days.setdefault(d, {"games": 0, "flights": []})
            entry["games"] += 1
            if fl["key"] not in entry["flights"]:
                entry["flights"].append(fl["key"])
    return {
        "season": season,
        "generatedAt": api.iso_now(),
        "days": dict(sorted(days.items())),
    }


def results_signature(games):
    """Score state of a flight; changes only when a result is entered or edited."""
    return sorted(
        (g.get("matchID"), g.get("hometeamscore"), g.get("awayteamscore"),
         g.get("hometeamPKscore"), g.get("awayteamPKscore"), api.game_date(g))
        for g in games
    )


def pending_result_flights(flights, today, max_age_days):
    """Flights with a past game still missing a score, within the chase window.

    The cap matters: 14 games from 2025-26 have been unscored for nearly a year.
    Without it they would trigger standings refreshes forever.
    """
    out = {}
    for fl in flights:
        n = 0
        for g in archived_games(fl["eventId"], fl["flightID"]):
            d = api.game_date(g)
            if not d or d >= today.isoformat() or api.has_score(g):
                continue
            age = (today - datetime.date.fromisoformat(d)).days
            if 0 < age <= max_age_days:
                n += 1
        if n:
            out[fl["key"]] = n
    return out


def flights_playing(calendar, flights, today, lookback_days):
    """Flights with a game in [today - lookback, today]."""
    want = set()
    for i in range(lookback_days + 1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        want.update((calendar.get("days", {}).get(d) or {}).get("flights") or [])
    return {fl["key"] for fl in flights if fl["key"] in want}


def is_match_day(calendar, today, padding_days):
    """True if today, or any of the previous `padding_days`, has fixtures.

    gameDate is a local wall-clock string with no timezone while cron runs in
    UTC, and teams span Pacific to Eastern — an 8pm Saturday kickoff in
    California is 03:00 Sunday UTC. The padding absorbs that.
    """
    for i in range(padding_days + 1):
        d = (today - datetime.timedelta(days=i)).isoformat()
        if (calendar.get("days") or {}).get(d):
            return True
    return False


def export_flight_csv(sources, season, fl):
    """Regenerate one flight's standings/schedule CSVs from the archive."""
    base = os.path.join(api.EXPORT_DIR, api.slug(season), api.slug(fl["conference"]))
    stem = f"{api.slug(fl['divisionName'])}-{api.slug(fl['flightName'])}"

    raw, _ = api.read_archive(api.p_standings(fl["divisionID"], fl["flightID"], fl["eventId"]))
    if raw:
        try:
            payload = json.loads(raw).get("data")
            block = payload[0] if isinstance(payload, list) and payload else (payload or {})
            rows = standings_rows(block.get("teamStandings") or [])
            if rows:
                write_csv(os.path.join(base, stem + ".standings.csv"), STANDINGS_COLUMNS, rows)
        except (ValueError, AttributeError, TypeError):
            pass

    rows = schedule_rows(archived_games(fl["eventId"], fl["flightID"]))
    if rows:
        write_csv(os.path.join(base, stem + ".schedule.csv"), SCHEDULE_COLUMNS, rows)


def refresh_policy(sources):
    p = sources.get("refresh") or {}
    md = p.get("matchDay") or {}
    return {
        "activeSeason": p.get("activeSeason") or next(iter(sources["seasons"])),
        "everyHours": md.get("everyHours", 2),
        "lookbackDays": md.get("lookbackDays", 2),
        "timezonePaddingDays": md.get("timezonePaddingDays", 1),
        "sweepDaily": (p.get("sweep") or {}).get("daily", True),
        "sweepAtUtcHour": (p.get("sweep") or {}).get("atUtcHour", 6),
        "maxPendingAgeDays": (p.get("pending") or {}).get("maxPendingAgeDays", 21),
        "minIntervalMinutes": p.get("minIntervalMinutes", 90),
    }


def cmd_refresh(sources, args):
    """Match-day driven incremental refresh. Returns a process exit code."""
    pol = refresh_policy(sources)
    season = pol["activeSeason"]
    if season not in sources["seasons"]:
        print(f"Active season {season!r} is not in the registry.")
        return 2

    today = (datetime.date.fromisoformat(args.date) if args.date
             else datetime.datetime.now(datetime.timezone.utc).date())
    now_hour = (args.at_hour if args.at_hour is not None
                else datetime.datetime.now(datetime.timezone.utc).hour)
    state = api.load_refresh_state()

    # Rate guard, so a manual re-run or a duplicated cron cannot hammer the API.
    last = state.get("updatedAt")
    if last and not args.force and not args.date:
        try:
            prev = datetime.datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ") \
                .replace(tzinfo=datetime.timezone.utc)
            mins = (datetime.datetime.now(datetime.timezone.utc) - prev).total_seconds() / 60
            if mins < pol["minIntervalMinutes"]:
                print(f"Last run was {mins:.0f} min ago "
                      f"(< minIntervalMinutes={pol['minIntervalMinutes']}). Nothing to do.")
                return 0
        except ValueError:
            pass

    flights = season_flights(sources, season)
    if not flights:
        print(f"No archived hierarchy for {season}; run: python archive.py --season {season}")
        return 2

    calendar = api.load_match_days()
    if calendar.get("season") != season:
        calendar = build_match_days(sources, season, flights)

    # A sweep is just the run that lands on the configured hour — or the first
    # run of a day that has not swept yet, so a missed cron self-heals.
    swept_on = state.get("lastSweepDate")
    due_by_hour = pol["sweepDaily"] and now_hour >= pol["sweepAtUtcHour"]
    sweep = bool(args.sweep or (due_by_hour and swept_on != today.isoformat()))

    match_day = is_match_day(calendar, today, pol["timezonePaddingDays"])
    pending = pending_result_flights(flights, today, pol["maxPendingAgeDays"])

    # ---- candidate set ----
    candidates = set()
    if sweep:
        candidates |= {fl["key"] for fl in flights}
    if match_day:
        candidates |= flights_playing(calendar, flights, today, pol["lookbackDays"])
    candidates |= set(pending)

    reason = ", ".join(filter(None, [
        "sweep" if sweep else "",
        "match day" if match_day else "",
        f"{len(pending)} flights with pending results" if pending else "",
    ])) or "nothing due"
    print(f"{today}  {season}  [{reason}]  -> {len(candidates)} of {len(flights)} flights")

    if not candidates:
        print("Non-match day, no sweep due, no pending results. No network calls.")
        return 0
    if args.dry_run:
        for fl in sorted((f for f in flights if f["key"] in candidates),
                         key=lambda f: (f["conference"], f["divisionName"])):
            print(f"  would refresh {fl['conference']:15} {fl['divisionName']:12} {fl['flightName']}")
        return 0

    stats = Stats()
    started = time.time()
    standings_refreshed = 0
    touched = set()

    # Hierarchies change rarely; refresh them only on a sweep, to catch new flights.
    if sweep:
        for conf, ev in (sources["seasons"][season].get("conferences") or {}).items():
            try:
                fetch_json(api.p_hierarchy(ev["eventId"]), stats)
            except api.ApiError as e:
                stats.fail(f"{season}/{conf}: hierarchy: {e}")
        flights = season_flights(sources, season) or flights

    by_key = {fl["key"]: fl for fl in flights}
    for key in sorted(candidates):
        fl = by_key.get(key)
        if not fl:
            continue
        label = f"{fl['conference']}/{fl['divisionName']}/{fl['flightName']}"

        before = results_signature(archived_games(fl["eventId"], fl["flightID"]))
        try:
            payload = fetch_json(api.p_schedule(fl["eventId"], fl["flightID"]), stats)
        except api.ApiError as e:
            stats.fail(f"{label}: schedule: {e}")
            continue
        games = payload.get("data") or []
        after = results_signature(games)
        touched.add(key)

        # Standings cannot move unless a result did — so only refetch when the
        # score signature actually changed, or when we have no standings at all.
        have_standings = api.read_archive(
            api.p_standings(fl["divisionID"], fl["flightID"], fl["eventId"]))[0] is not None
        if after != before or not have_standings:
            try:
                fetch_json(api.p_standings(fl["divisionID"], fl["flightID"], fl["eventId"]), stats)
                standings_refreshed += 1
            except api.ApiError as e:
                stats.fail(f"{label}: standings: {e}")

    # Keep the CSV exports in step with the JSON we just refreshed. Local work
    # only, no API cost.
    for key in sorted(touched):
        fl = by_key.get(key)
        if fl:
            export_flight_csv(sources, season, fl)

    # Rebuild the calendar from whatever is now on disk.
    calendar = build_match_days(sources, season, flights)
    api.write_json_file(api.MATCH_DAYS_PATH, calendar)

    still_pending = pending_result_flights(flights, today, pol["maxPendingAgeDays"])
    elapsed = time.time() - started
    api.write_json_file(api.REFRESH_STATE_PATH, {
        "updatedAt": api.iso_now(),
        "activeSeason": season,
        "runDate": today.isoformat(),
        "sweep": sweep,
        "matchDay": match_day,
        "lastSweepDate": today.isoformat() if sweep else swept_on,
        "flightsConsidered": len(flights),
        "flightsRefreshed": len(candidates),
        "standingsRefreshed": standings_refreshed,
        "requests": stats.fetched,
        "failed": stats.failed,
        "durationSeconds": round(elapsed, 1),
        "pendingResultFlights": len(still_pending),
        "pendingResultGames": sum(still_pending.values()),
    })

    print(f"{stats.fetched} requests ({standings_refreshed} standings), "
          f"{stats.failed} failed, {elapsed:.0f}s. "
          f"Pending results: {sum(still_pending.values())} games "
          f"in {len(still_pending)} flights.")
    if stats.errors:
        for e in stats.errors[:10]:
            print(f"  - {e}")
    return 1 if stats.failed else 0


def load_manifest():
    if os.path.exists(api.MANIFEST_PATH):
        try:
            with open(api.MANIFEST_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except ValueError:
            pass
    return {"updated": None, "events": {}}


def save_manifest(manifest):
    os.makedirs(api.ARCHIVE_DIR, exist_ok=True)
    manifest["updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(api.MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")


def main():
    ap = argparse.ArgumentParser(description="Archive ECNL standings and schedules locally.")
    ap.add_argument("--season", help="Season key, e.g. 2024-25. Defaults to the newest season.")
    ap.add_argument("--conference", help="Single conference name, e.g. Texas.")
    ap.add_argument("--all", action="store_true", help="Every season in the registry.")
    ap.add_argument("--verify", action="store_true",
                    help="Only check that event IDs resolve to the expected names.")
    ap.add_argument("--dry-run", action="store_true", help="List what would be fetched.")
    ap.add_argument("--force", action="store_true",
                    help="Re-fetch even if the archived copy is less than 12h old.")
    ap.add_argument("--no-update-sources", action="store_true",
                    help="Do not write derived age-group birth years back to data/sources.json.")
    ap.add_argument("--refresh", action="store_true",
                    help="Match-day driven incremental refresh of the active season "
                         "(what the scheduled workflow runs).")
    ap.add_argument("--sweep", action="store_true",
                    help="With --refresh: force the all-flights schedule sweep.")
    ap.add_argument("--date", metavar="YYYY-MM-DD",
                    help="With --refresh: pretend today is this date (for testing).")
    ap.add_argument("--at-hour", type=int, metavar="H",
                    help="With --refresh: pretend the current UTC hour is H (for testing).")
    args = ap.parse_args()

    try:
        sources = api.load_sources()
    except (OSError, ValueError) as e:
        print(f"Could not read {api.SOURCES_PATH}: {e}")
        return 2

    if args.refresh:
        return cmd_refresh(sources, args)

    season_keys = list(sources["seasons"].keys())
    if args.all:
        season = None
    elif args.season:
        if args.season not in sources["seasons"]:
            print(f"Unknown season {args.season!r}. Known: {', '.join(season_keys)}")
            return 2
        season = args.season
    else:
        season = season_keys[0]
        print(f"No --season/--all given; defaulting to {season}.\n")

    if args.verify:
        return verify(sources, season, args.conference)

    stats = Stats()
    manifest = load_manifest()
    started = time.time()

    age_changes = []
    for season_key, kind, name, event in api.iter_events(sources, season, args.conference):
        entry = archive_event(sources, season_key, kind, name, event, stats, args.force, args.dry_run)
        if entry:
            div_team_names = entry.pop("_divTeamNames", {})
            if kind == "conference" and not args.no_update_sources:
                divisions = [{"divisionName": d["divisionName"]} for d in entry["divisions"]]
                age_changes += update_age_groups(
                    sources, season_key, divisions, div_team_names)
            manifest["events"][f"{season_key}/{name}"] = entry
            save_manifest(manifest)  # checkpoint, so an interrupted crawl keeps progress

    if age_changes and not args.dry_run:
        save_sources(sources)
        print(f"\nBirth-year anchor updated in {api.SOURCES_PATH}:")
        for c in age_changes:
            print(f"  {c}")

    elapsed = time.time() - started
    print(f"\nFetched {stats.fetched}, reused {stats.skipped} fresh, "
          f"{stats.failed} failed, in {elapsed:.0f}s.")
    if stats.errors:
        print("\nProblems:")
        for e in stats.errors:
            print(f"  - {e}")
    if not args.dry_run:
        print(f"\nArchive: {api.ARCHIVE_API_DIR}\nCSVs:    {api.EXPORT_DIR}")
    return 1 if stats.failed else 0


if __name__ == "__main__":
    sys.exit(main())
