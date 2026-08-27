"""
Shared helpers for the ECNL dashboard: API access, the source registry, and the
on-disk archive layout. Used by both proxy_server.py and archive.py.

Zero dependencies (stdlib only).
"""

import collections
import json
import os
import re
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
SOURCES_PATH = os.path.join(ROOT, "data", "sources.json")
ARCHIVE_DIR = os.path.join(ROOT, "archive")
ARCHIVE_API_DIR = os.path.join(ARCHIVE_DIR, "api")
MANIFEST_PATH = os.path.join(ARCHIVE_DIR, "manifest.json")
EXPORT_DIR = os.path.join(ROOT, "export")

# Override to point at a different host, or at an unreachable one to exercise
# the archive-fallback path: ECNL_API_BASE=https://127.0.0.1:9 python proxy_server.py
API_BASE = os.environ.get("ECNL_API_BASE", "https://api.athleteone.com")

# The public TGS site is the Referer/Origin the API expects.
API_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0",
    "Origin": "https://public.totalglobalsports.com",
    "Referer": "https://public.totalglobalsports.com/",
}

# Archive paths are derived from request paths, so they must be strictly
# validated: only these characters may appear in a proxied API path.
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/-]+$")


# ---------- source registry ----------

def load_sources(path=SOURCES_PATH):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def iter_events(sources, season=None, conference=None, include_national=True):
    """Yield (season, kind, name, event_dict) for each configured event.

    kind is "conference" or "national".
    """
    for season_key, season_data in sources["seasons"].items():
        if season and season_key != season:
            continue
        for name, event in (season_data.get("conferences") or {}).items():
            if conference and name != conference:
                continue
            yield season_key, "conference", name, event
        if include_national and not conference:
            for name, event in (season_data.get("national") or {}).items():
                yield season_key, "national", name, event


# ---------- archive paths ----------

def is_safe_api_path(path):
    """True if `path` (no leading slash, no `/api/` prefix) is safe to map to disk."""
    if not path or not _SAFE_PATH.match(path):
        return False
    if path.startswith("/") or ".." in path.split("/"):
        return False
    return True


def archive_path_for(api_path):
    """Map an API path such as `Event/get-schedules-by-flight/3925/32621/0`
    to `archive/api/Event/get-schedules-by-flight/3925/32621/0.json`.

    Returns None if the path is unsafe.
    """
    if not is_safe_api_path(api_path):
        return None
    full = os.path.normpath(os.path.join(ARCHIVE_API_DIR, api_path + ".json"))
    # Defence in depth: the result must stay inside the archive dir.
    if not full.startswith(ARCHIVE_API_DIR + os.sep):
        return None
    return full


def write_archive(api_path, raw_bytes):
    """Atomically write a raw JSON response into the archive. Returns the path
    written, or None if the path was rejected."""
    dest = archive_path_for(api_path)
    if dest is None:
        return None
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "wb") as f:
        f.write(raw_bytes)
    os.replace(tmp, dest)
    return dest


def read_archive(api_path):
    """Return (raw_bytes, mtime_iso) for an archived response, or (None, None)."""
    src = archive_path_for(api_path)
    if src is None or not os.path.exists(src):
        return None, None
    with open(src, "rb") as f:
        data = f.read()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(os.path.getmtime(src))) + "Z"
    return data, stamp


def archive_age_seconds(api_path):
    """Seconds since the archived copy was written, or None if absent."""
    src = archive_path_for(api_path)
    if src is None or not os.path.exists(src):
        return None
    return time.time() - os.path.getmtime(src)


# ---------- API access ----------

class ApiError(Exception):
    pass


def fetch_api_raw(api_path, timeout=20, retries=3, backoff=1.5):
    """GET `<API_BASE>/api/<api_path>` and return raw bytes.

    Retries on 5xx and transport errors; 4xx fails immediately.
    """
    url = f"{API_BASE}/api/{api_path}"
    delay = backoff
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=API_HEADERS)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = ApiError(f"HTTP {e.code} for {api_path}")
            if e.code < 500:
                raise last  # client error: retrying will not help
        except Exception as e:  # URLError, socket.timeout, ...
            last = ApiError(f"{type(e).__name__}: {e} for {api_path}")
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= backoff
    raise last


def fetch_api(api_path, **kwargs):
    """GET an API path and return parsed JSON."""
    raw = fetch_api_raw(api_path, **kwargs)
    try:
        return json.loads(raw)
    except ValueError as e:
        raise ApiError(f"Invalid JSON from {api_path}: {e}")


# ---------- endpoint path builders ----------

def p_hierarchy(event_id):
    return f"Event/get-event-schedule-or-standings/{event_id}"


def p_standings(division_id, flight_id, event_id):
    return f"Event/get-standings-by-div-and-flight/{division_id}/{flight_id}/{event_id}"


def p_schedule(event_id, flight_id):
    # Third segment is a club filter; 0 means "all clubs".
    return f"Event/get-schedules-by-flight/{event_id}/{flight_id}/0"


def p_brackets(event_id, flight_id):
    return f"Event/get-flight-brackets-by-flight/{event_id}/{flight_id}"


def p_event_details(event_id):
    return f"Event/get-event-details-by-eventID/{event_id}"


# ---------- misc ----------

# ---------- age groups / birth-year anchor ----------
#
# Divisions are named two different ways depending on the season:
#   birth year : "G2011", "G2008/2007"      (2022-23 .. 2025-26)
#   age        : "GU13", "GU18/19"          (2021-22, and 2026-27 onward)
#
# From 2026-27 each age group spans two calendar birth years (ECNL moved to
# school-year cohorts), so a single birth year can map to two age groups.
# The birth-year band is therefore the stable anchor across seasons, and we
# resolve it from the most authoritative source available.

_DIV_YEARS = re.compile(r"G(\d{4})(?:\s*/\s*(\d{2,4}))?")
_DIV_U = re.compile(r"^GU(\d{1,2})")
# Combined age groups carry more than one U number: "GU18/U19", "GU18/19"
_DIV_ALL_U = re.compile(r"U?(\d{1,2})")
_TEAM_BAND = re.compile(r"\bG\s?(\d{2,4})\s*/\s*(\d{2,4})\b|\bG\s?(\d{2,4})\b")


def _norm_year(y):
    y = int(y)
    return 2000 + y if y < 100 else y


def division_u(division_name, start_year):
    """The U-number for a division, whichever naming scheme it uses."""
    m = _DIV_U.match(division_name)
    if m:
        return int(m.group(1))
    m = _DIV_YEARS.search(division_name)
    if m:
        return start_year + 1 - int(m.group(1))
    return None


def band_from_division_name(division_name):
    """Birth years embedded in the division name itself, e.g. G2008/2007."""
    m = _DIV_YEARS.search(division_name)
    if not m:
        return None
    years = {_norm_year(m.group(1))}
    if m.group(2):
        years.add(_norm_year(m.group(2)))
    return tuple(sorted(years))


def band_from_team_name(team_name):
    """Birth years embedded in a team name, e.g. 'ECNL G2013/14' or 'ECNL G08/07'."""
    for a, b, c in _TEAM_BAND.findall(team_name):
        if a:
            return tuple(sorted({_norm_year(a), _norm_year(b)}))
        if c and (len(c) == 4 or int(c) <= 30):
            return (_norm_year(c),)
    return None


def band_from_team_names(names, threshold=0.6):
    """Modal birth-year band across a division's teams, if a clear majority agree."""
    found = [b for b in (band_from_team_name(n) for n in names) if b]
    if not found:
        return None
    band, count = collections.Counter(found).most_common(1)[0]
    return band if count >= max(1, len(names) * threshold) else None


def resolve_age_group(division_name, start_year, team_names=(), two_year_bands=None):
    """Resolve a division to {u, birthYears, source}.

    Resolution order, most authoritative first:
      1. birth years written in the division name   -> "division-name"
      2. birth years written in the team names      -> "team-names"
      3. computed from the U-number and the season  -> "computed"

    `two_year_bands` only affects step 3: when True the computed band spans two
    calendar years (2026-27 onward), otherwise a single year.
    """
    u = division_u(division_name, start_year)

    band = band_from_division_name(division_name)
    source = "division-name"
    if not band:
        band = band_from_team_names(list(team_names))
        source = "team-names"
    if not band:
        if u is None:
            return None
        # A combined group such as "GU18/U19" covers every U number it names.
        us = [int(x) for x in _DIV_ALL_U.findall(division_name[1:])] or [u]
        years = set()
        for each in us:
            years.add(start_year + 1 - each)
            if two_year_bands:
                years.add(start_year - each)
        band = tuple(sorted(years))
        source = "computed"

    return {"u": u, "birthYears": list(band), "source": source}


def slug(text):
    """Filesystem-safe version of a division/flight/conference name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-") or "unnamed"


def unwrap(payload):
    """TGS responses are {"result": "success", "data": ...} — return `data`."""
    if isinstance(payload, dict) and "data" in payload:
        return payload["data"]
    return payload
