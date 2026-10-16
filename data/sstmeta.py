"""The "meta" dict that travels with every SST week: what was served, from where, how fresh.

It is part of the dcc.Store payload (sst-store) and of every disk-cache entry. Contract
(docs/sst1 BRIEF, kept in CLAUDE.md):

  source            "MUR" | "OISST"  (what was actually served)
  source_label      "MUR 1 km (NASA JPL, via NOAA CoastWatch)" | "OISST 25 km (NOAA NCEI)"
  resolution_km     1 | 25
  dataset_id, server  the ERDDAP dataset and host it came from
  days              ISO dates actually served (7 normally; fewer if a day was missing)
  requested_end     the end date the reader asked for; served_end: the last day served
  latest_available  the newest MUR day NOAA had (ISO date; refreshed hourly on cache hits)
  built_at          ISO UTC timestamp of when the data was fetched from NOAA
  from_cache        true when this came from the disk cache

Extras (lane D): missing_days (dates of the asked-for window not served), complete (false
when a missing day may still come: NOAA was busy), preliminary_days (days that were under
4 days old when fetched: MUR revises those), week_match ("exact"; "newest" = asked past the
newest MUR day; "closest_saved" = a nearby saved week was served instead; "older_copy" = a
refresh failed and the saved copy was served), queued (the week is on the seeder's list).
Old cache entries without "meta" still load: meta_for_entry() fills in what it can.
"""

from datetime import date, datetime, timezone
from typing import Dict, Iterable, Optional

SOURCES = {
    "MUR": {"source_label": "MUR 1 km (NASA JPL, via NOAA CoastWatch)", "resolution_km": 1},
    "OISST": {"source_label": "OISST 25 km (NOAA NCEI)", "resolution_km": 25},
}
PRELIMINARY_DAYS = 4   # MUR's final version replaces the first one about four days later
REVISION_DAYS = 7      # ERDDAP: "the data for the most recent 7 days is usually revised everyday"
RETRY_HOURS = 6        # an OISST or incomplete week is retried for MUR after this long
REFRESH_HOURS_PRELIM = 12   # a week with preliminary days is refetched after this long
REFRESH_HOURS_REVISION = 24  # a week inside MUR's 7-day revision window: after this long

FIELDS = ("source", "source_label", "resolution_km", "dataset_id", "server", "days",
          "requested_end", "served_end", "latest_available", "built_at", "from_cache")


def iso(d) -> Optional[str]:
    if d is None or d == "":
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    return str(d)[:10]


def utc_iso(ts: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts, timezone.utc) if ts is not None else datetime.now(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_utc(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def source_of(dataset_id: Optional[str]) -> str:
    return "OISST" if "oisst" in (dataset_id or "").lower() else "MUR"


def preliminary(days: Iterable[str], built_at: Optional[str]) -> list:
    built = parse_utc(built_at)
    if not built:
        return []
    b = built.date()
    out = []
    for d in days:
        try:
            if (b - date.fromisoformat(d)).days < PRELIMINARY_DAYS:
                out.append(d)
        except ValueError:
            pass
    return out


def make_meta(source: str, dataset_id: str, server: str, days, requested_end=None,
              latest_available=None, built_at: Optional[str] = None, from_cache: bool = False,
              missing_days=(), complete: bool = True, week_match: str = "exact") -> Dict:
    info = SOURCES.get(source) or SOURCES["MUR"]
    days = sorted(iso(d) for d in days if d)
    built = built_at or utc_iso()
    return {
        "source": source if source in SOURCES else "MUR",
        "source_label": info["source_label"],
        "resolution_km": info["resolution_km"],
        "dataset_id": dataset_id or "",
        "server": server or "",
        "days": days,
        "requested_end": iso(requested_end) or (days[-1] if days else None),
        "served_end": days[-1] if days else None,
        "latest_available": iso(latest_available),
        "built_at": built,
        "from_cache": bool(from_cache),
        "missing_days": sorted(iso(d) for d in (missing_days or [])),
        "complete": bool(complete),
        "preliminary_days": preliminary(days, built),
        "week_match": week_match,
        "queued": False,
    }


def meta_from_fetch(sst: Dict, built_at: Optional[str] = None) -> Dict:
    """Meta for a week that get_sst_multiday() just returned."""
    return make_meta(
        sst.get("source") or source_of(sst.get("dataset_id")),
        sst.get("dataset_id"), sst.get("server"),
        [d["date"] for d in sst.get("days", [])],
        requested_end=sst.get("requested_end"),
        latest_available=sst.get("latest_available"),
        built_at=built_at,
        missing_days=sst.get("missing_days") or [],
        complete=sst.get("complete", True),
    )


def meta_for_entry(entry: Dict, mtime: Optional[float] = None) -> Dict:
    """The stored meta of a cache entry, or one rebuilt from an old entry's own fields."""
    stored = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    days = stored.get("days") or entry.get("dates") or []
    dsid = stored.get("dataset_id") or entry.get("dataset_id") or ""
    source = stored.get("source") or source_of(dsid)
    if not dsid and source == "MUR":
        dsid = "jplMURSST41"
    built = stored.get("built_at") or (utc_iso(mtime) if mtime else None)
    m = make_meta(source, dsid, stored.get("server") or entry.get("server") or "",
                  days, requested_end=stored.get("requested_end"),
                  latest_available=stored.get("latest_available"), built_at=built,
                  missing_days=stored.get("missing_days") or [],
                  complete=stored.get("complete", True))
    if not built:
        m["built_at"] = None
        m["preliminary_days"] = []
    return m


def for_serving(meta: Dict, requested_end=None, latest_available=None, from_cache: bool = False,
                week_match: Optional[str] = None, queued: Optional[bool] = None) -> Dict:
    """A copy of meta updated for this request."""
    m = dict(meta)
    if requested_end is not None:
        m["requested_end"] = iso(requested_end)
    if latest_available is not None:
        m["latest_available"] = iso(latest_available)
    m["from_cache"] = bool(from_cache)
    if week_match:
        m["week_match"] = week_match
    if queued is not None:
        m["queued"] = bool(queued)
    return m


def refresh_due(meta: Dict, now: Optional[datetime] = None) -> bool:
    """True when a saved week should be fetched from NOAA again (serve the saved copy if that fails).

    OISST weeks and weeks with days NOAA didn't send in time: after RETRY_HOURS. MUR weeks fetched
    while some days were preliminary: after REFRESH_HOURS_PRELIM; while inside MUR's 7-day revision
    window: after REFRESH_HOURS_REVISION. Anything fetched 7+ days after its last day is final.
    """
    built = parse_utc(meta.get("built_at"))
    if built is None:
        return False  # an old entry without a date: treat as final, as the old cache did
    now = now or datetime.now(timezone.utc)
    age_h = (now - built).total_seconds() / 3600
    if meta.get("source") != "MUR" or not meta.get("complete", True):
        return age_h >= RETRY_HOURS
    try:
        lag = (built.date() - date.fromisoformat(meta.get("served_end") or "")).days
    except ValueError:
        return False
    if lag < PRELIMINARY_DAYS:
        return age_h >= REFRESH_HOURS_PRELIM
    if lag < REVISION_DAYS:
        return age_h >= REFRESH_HOURS_REVISION
    return False
