"""
ECNL Dashboard archiver (zero-dependency).

Crawls every event listed in data/sources.json and writes:
  archive/api/<endpoint-path>.json   raw API mirror (what the dashboard reads offline)
  export/<season>/<conference>/*.csv human-readable standings & schedules
  archive/manifest.json              index tying event IDs back to season/conference

Usage:
    python archive.py --verify --all              check every event ID resolves (no data fetch)
    python archive.py                             archive the newest season
    python archive.py --season 2024-25
    python archive.py --season 2024-25 --conference Texas
    python archive.py --all                       archive every season (~1200+ requests)
    python archive.py --all --force               ignore the freshness check
"""

import argparse
import csv
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
    args = ap.parse_args()

    try:
        sources = api.load_sources()
    except (OSError, ValueError) as e:
        print(f"Could not read {api.SOURCES_PATH}: {e}")
        return 2

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
