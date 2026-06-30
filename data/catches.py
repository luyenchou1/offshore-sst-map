"""GotOne catch data loader and filter.

Reads `data/gotone_catches.csv` (preprocessed: AOI-clipped, skunks dropped,
PII stripped, lat/lon snapped to 2 decimals ~1km grid) and `data/gotone_species.csv`
(species_id -> name lookup). Provides filtering by species group + date window
and conversion to GeoJSON for the Leaflet overlay.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

# Catch times are stored UTC; anglers think in Eastern Time. Display in ET.
_ET = ZoneInfo("America/New_York")

# Catch CSVs live here. Defaults to this repo's data/ dir (local dev); set
# CATCH_DATA_DIR to a persistent path on Render (e.g. the disk at /var/data/cache)
# so uploaded catch data survives restarts and deploys.
_DATA_DIR = os.environ.get("CATCH_DATA_DIR") or os.path.dirname(__file__)
_CATCHES_CSV = os.path.join(_DATA_DIR, "gotone_catches.csv")
_SPECIES_CSV = os.path.join(_DATA_DIR, "gotone_species.csv")

# Public aliases + allow-list for the authenticated upload endpoint (Firebase sync).
CATCHES_CSV_PATH = _CATCHES_CSV
SPECIES_CSV_PATH = _SPECIES_CSV
ALLOWED_UPLOAD_FILES = {
    "gotone_catches.csv": _CATCHES_CSV,
    "gotone_species.csv": _SPECIES_CSV,
}

# Species that are not real catches (no fish landed) — dropped on load.
SKUNK_SPECIES_ID = "1ZIZ6YritSABYbsTg1EJ"
# Implausible-value guards (per GotOne data-quality notes). Out-of-range
# readings are nulled (the catch is still valid), not dropped.
_TEMP_MIN_F, _TEMP_MAX_F = 32.0, 85.0
_LEN_MIN_IN, _LEN_MAX_IN = 1.0, 80.0

# Species grouping (id -> group key). Catches whose species_id is not in any
# group fall through to "other".
SPECIES_GROUPS = {
    "inshore": {
        "label": "Bass / Blues",
        "color": "#e63946",
        "species_ids": {
            "7Xuawz7RA66GMTaHkkuh",  # Striped bass
            "tXqH4TTzXpgtQbcfB8mJ",  # Bluefish
            "5UOZx47ogcMrmiVzylNR",  # Weakfish
        },
    },
    "bottom": {
        "label": "Fluke / Tog / Sea bass",
        "color": "#f4a261",
        "species_ids": {
            "44n9nYw5PHTdJWftn1kJ",  # Fluke
            "gAdaG8wQwAfPN7hxf2r7",  # Black sea bass
            "u68yzJ6stJwINy5ayc0F",  # Tautog
            "PPStCWVDXN0r6b045SwJ",  # Scup
        },
    },
    "tuna": {
        "label": "Tuna",
        "color": "#9d4edd",
        "species_ids": {
            "nS2Hkn7CSFxT4ajrNxbb",  # Bluefin
            "nMlCXzkZGYtyhRsc5mJy",  # Yellowfin
            "Md0xqjw2ohI7uPcGXroV",  # Bigeye
            "fAnZRVmeF8FuGnEW2UgE",  # Blackfin
            "iAyJyI4n8VpHv1ZRN16h",  # Skipjack
            "R1GGHlyouGRKSzzNXhEg",  # Albacore (longfin)
            "QbRxb5eItpoqq52kFr5x",  # Atlantic bonito
        },
    },
    "false_albie": {
        "label": "False albacore",
        "color": "#00b4d8",
        "species_ids": {
            "UkDGO7fYMg0a0JKSnsyu",  # False albacore
        },
    },
    "pelagic": {
        "label": "Pelagic / Mahi / Billfish",
        "color": "#ffd60a",
        "species_ids": {
            "k5VeUiU8SBy8FHg4YwXy",  # Mahi-mahi
            "eaPKuT1AEq90R9tmLp3B",  # Blue marlin
            "lRe4fLGWX9YG87DoG0Bq",  # Striped marlin
            "AAhVIobcj4ANmMaNswlK",  # Cobia
            "GnutJi2chtL0sqVGoQn6",  # King mackerel
            "wnFLqAf12P4u1fclxuoh",  # Spanish mackerel
            "GstnPoxE6jcieXuNNChI",  # Atlantic mackerel
            "NANTLI8GhMOVTxJRWUpT",  # Chub mackerel
        },
    },
    "other": {
        "label": "Other",
        "color": "#9aa0a6",
        "species_ids": set(),  # catch-all
    },
}

GROUP_ORDER = ["inshore", "bottom", "tuna", "false_albie", "pelagic", "other"]


def get_group_options():
    """Return checklist options [{label, value}] for the picker."""
    return [
        {"label": f" {SPECIES_GROUPS[g]['label']}", "value": g}
        for g in GROUP_ORDER
    ]


def get_all_groups():
    return list(GROUP_ORDER)


def _group_for(species_id: str) -> str:
    for g, meta in SPECIES_GROUPS.items():
        if species_id in meta["species_ids"]:
            return g
    return "other"


# Module-level cache
_species_names: dict[str, str] = {}
_catches: list[dict] = []


def load_catches() -> int:
    """Load species + catches CSVs into memory. Returns count loaded."""
    global _species_names, _catches

    _species_names = {}
    if os.path.exists(_SPECIES_CSV):
        with open(_SPECIES_CSV) as f:
            for row in csv.DictReader(f):
                _species_names[row["__id__"]] = row["name"]

    _catches = []
    if not os.path.exists(_CATCHES_CSV):
        print(f"[catches] WARNING: {_CATCHES_CSV} not found, layer will be empty")
        return 0

    with open(_CATCHES_CSV) as f:
        for row in csv.DictReader(f):
            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
            except (TypeError, ValueError):
                continue
            ts = row.get("catch_time", "")
            try:
                # ISO format, may end in Z
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                continue
            sid = row["species_id"]
            if sid == SKUNK_SPECIES_ID:
                continue  # no fish landed — not a catch
            grp = _group_for(sid)
            try:
                length = float(row["fish_length"]) if row.get("fish_length") else None
            except ValueError:
                length = None
            if length is not None and not (_LEN_MIN_IN <= length <= _LEN_MAX_IN):
                length = None  # implausible — drop the value, keep the catch
            try:
                temp = float(row["water_temperature"]) if row.get("water_temperature") else None
            except ValueError:
                temp = None
            if temp is not None and not (_TEMP_MIN_F <= temp <= _TEMP_MAX_F):
                temp = None  # implausible — drop the value, keep the catch
            # Convert to Eastern Time so season fields + tooltip read local.
            d_utc = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            et = d_utc.astimezone(_ET)
            et_label = (f"{et.strftime('%b')} {et.day}, {et.year} "
                        f"{et.strftime('%I:%M %p').lstrip('0')} ET")
            _catches.append({
                "lat": lat,
                "lon": lon,
                "dt": dt,
                "year": et.year,
                "month": et.month,
                "day": et.day,
                "doy": min(et.timetuple().tm_yday, 365),
                "et_label": et_label,
                "species_id": sid,
                "species_name": _species_names.get(sid, "Unknown"),
                "group": grp,
                "length": length,
                "temp": temp,
            })

    print(f"[catches] Loaded {len(_catches)} catches across {len(SPECIES_GROUPS)} groups")
    return len(_catches)


def get_catches_filtered(
    groups: Optional[list[str]] = None,
    date_mode: str = "all",
    window_start: Optional[date] = None,
    window_end: Optional[date] = None,
) -> list[dict]:
    """Filter catches by species groups and date mode.

    date_mode:
      - "all": no date filter
      - "window": catches within [window_start, window_end]
      - "window_any_year": catches whose (month, day) falls within the
        window's month/day range, across all years
    """
    if groups is None:
        groups = GROUP_ORDER
    group_set = set(groups)

    if date_mode == "all" or window_start is None or window_end is None:
        return [c for c in _catches if c["group"] in group_set]

    if date_mode == "window":
        ws = datetime.combine(window_start, datetime.min.time())
        we = datetime.combine(window_end, datetime.max.time())
        out = []
        for c in _catches:
            if c["group"] not in group_set:
                continue
            cdt = c["dt"].replace(tzinfo=None) if c["dt"].tzinfo else c["dt"]
            if ws <= cdt <= we:
                out.append(c)
        return out

    if date_mode == "window_any_year":
        # Build set of (month, day) tuples in the window range
        md_set: set[tuple[int, int]] = set()
        d = window_start
        while d <= window_end:
            md_set.add((d.month, d.day))
            d += timedelta(days=1)
        return [
            c for c in _catches
            if c["group"] in group_set and (c["month"], c["day"]) in md_set
        ]

    return []


def aggregate_to_grid(
    rows: list[dict],
    cell_size: float = 0.05,
    k_min: int = 3,
) -> list[dict]:
    """Bin catches into a grid of `cell_size` degrees and apply k-anonymity.

    Returns one cell per (group, lat_idx, lon_idx) bin with count >= k_min.
    Cells with fewer than k_min catches are dropped to prevent any single
    catch from being identified.
    """
    bins: dict[tuple[str, int, int], dict] = {}
    for c in rows:
        gi = c["group"]
        lat_idx = int(round(c["lat"] / cell_size))
        lon_idx = int(round(c["lon"] / cell_size))
        key = (gi, lat_idx, lon_idx)
        b = bins.get(key)
        if b is None:
            b = {
                "group": gi,
                "lat_idx": lat_idx,
                "lon_idx": lon_idx,
                "count": 0,
                "max_length": None,
                "temps": [],
                "latest": None,
            }
            bins[key] = b
        b["count"] += 1
        if c["length"] is not None:
            if b["max_length"] is None or c["length"] > b["max_length"]:
                b["max_length"] = c["length"]
        if c["temp"] is not None:
            b["temps"].append(c["temp"])
        if b["latest"] is None or c["dt"] > b["latest"]:
            b["latest"] = c["dt"]

    cells = []
    for b in bins.values():
        if b["count"] < k_min:
            continue
        avg_temp = sum(b["temps"]) / len(b["temps"]) if b["temps"] else None
        cells.append({
            "group": b["group"],
            "lat_center": b["lat_idx"] * cell_size,
            "lon_center": b["lon_idx"] * cell_size,
            "cell_size": cell_size,
            "count": b["count"],
            "max_length": b["max_length"],
            "avg_temp": avg_temp,
            "latest": b["latest"],
        })
    return cells


def cells_to_geojson(cells: list[dict]) -> dict:
    """Convert aggregated grid cells to GeoJSON Polygon features."""
    features = []
    # Find max count per group for opacity scaling
    max_by_group: dict[str, int] = {}
    for c in cells:
        g = c["group"]
        if c["count"] > max_by_group.get(g, 0):
            max_by_group[g] = c["count"]

    for c in cells:
        half = c["cell_size"] / 2
        lat = c["lat_center"]
        lon = c["lon_center"]
        ring = [
            [lon - half, lat - half],
            [lon + half, lat - half],
            [lon + half, lat + half],
            [lon - half, lat + half],
            [lon - half, lat - half],
        ]
        color = SPECIES_GROUPS[c["group"]]["color"]
        # Opacity 0.25 .. 0.85 scaled by log(count) for better dynamic range
        import math
        mx = max(max_by_group.get(c["group"], 1), 1)
        rel = math.log1p(c["count"]) / math.log1p(mx) if mx > 1 else 1.0
        opacity = 0.25 + 0.6 * rel
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {
                "group": c["group"],
                "color": color,
                "opacity": round(opacity, 3),
                "count": c["count"],
                "max_length": c["max_length"],
                "avg_temp": round(c["avg_temp"], 1) if c["avg_temp"] is not None else None,
                "latest": c["latest"].strftime("%Y-%m-%d") if c["latest"] else None,
                "label": SPECIES_GROUPS[c["group"]]["label"],
            },
        })
    return {"type": "FeatureCollection", "features": features}


def catches_to_geojson(rows: list[dict]) -> dict:
    """Convert filtered catches list to a GeoJSON FeatureCollection."""
    features = []
    for c in rows:
        color = SPECIES_GROUPS[c["group"]]["color"]
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [c["lon"], c["lat"]],
            },
            "properties": {
                "species": c["species_name"],
                "group": c["group"],
                "color": color,
                "length": c["length"],
                "temp": c["temp"],
                "date": c["dt"].strftime("%Y-%m-%d"),
            },
        })
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# Catch-map page: filter options + compact animation payload
# ---------------------------------------------------------------------------

def get_groups_meta() -> list[dict]:
    """Group metadata (key, label, color) in display order — for legend + JS."""
    return [
        {"key": g, "label": SPECIES_GROUPS[g]["label"], "color": SPECIES_GROUPS[g]["color"]}
        for g in GROUP_ORDER
    ]


def get_species_options() -> list[dict]:
    """Individual-species dropdown options for species present in the data."""
    seen: dict[str, str] = {}
    for c in _catches:
        seen[c["species_id"]] = c["species_name"]
    opts = [{"label": name, "value": sid} for sid, name in seen.items()]
    opts.sort(key=lambda o: o["label"])
    return opts


def get_year_options() -> list[int]:
    """Distinct catch years, newest first."""
    return sorted({c["year"] for c in _catches}, reverse=True)


def _doy_label(start_doy: int, end_doy: int) -> str:
    """Human label for a day-of-year window, e.g. 'Apr 3 – Apr 16' (or 'Apr 3')."""
    base = date(2023, 1, 1)
    s = base + timedelta(days=start_doy - 1)
    if start_doy == end_doy:
        return f"{s.strftime('%b')} {s.day}"
    e = base + timedelta(days=end_doy - 1)
    return f"{s.strftime('%b')} {s.day} – {e.strftime('%b')} {e.day}"


# Frame windows by time grain. Each frame is an inclusive [startDoy, endDoy]
# day-of-year window; the slider scrubs them (static view) and Play animates.
GRAINS = ("season", "month", "week", "day")


def _frames_for_grain(grain: str):
    """Return (frames, labels) for the requested time grain."""
    if grain == "month":
        frames, labels = [], []
        for m in range(1, 13):
            start = date(2023, m, 1)
            end = date(2023, 12, 31) if m == 12 else date(2023, m + 1, 1) - timedelta(days=1)
            frames.append([start.timetuple().tm_yday, min(end.timetuple().tm_yday, 365)])
            labels.append(start.strftime("%B"))
        return frames, labels
    if grain == "week":
        frames, labels, start = [], [], 1
        while start <= 365:
            end = min(start + 6, 365)
            frames.append([start, end])
            labels.append(_doy_label(start, end))
            start += 7
        return frames, labels
    if grain == "day":
        frames = [[d, d] for d in range(1, 366)]
        labels = [_doy_label(d, d) for d in range(1, 366)]
        return frames, labels
    # "season" (default): one window covering the whole year -> all catches.
    return [[1, 365]], ["Full season"]


def build_catch_payload(
    groups: Optional[list[str]] = None,
    species_ids: Optional[list[str]] = None,
    years: Optional[list[int]] = None,
    grain: str = "season",
) -> dict:
    """Compact payload for the catch map + seasonal-migration animation.

    `points` rows are arrays (small payload):
        [lat, lon, groupIdx, length|None, temp|None, doy, speciesIdx, "Mon D, YYYY h:mm AM ET"]
    `frames` are day-of-year windows ([startDoy, endDoy]) sized by `grain`
    (season = one full-year window = all catches; month / week / day = smaller
    windows the slider scrubs and Play animates). Points are pooled across the
    selected years so the window reads as a seasonal migration. The heatmap is
    binned clientside from these same points.
    """
    group_set = set(groups) if groups else set(GROUP_ORDER)
    sid_set = set(species_ids) if species_ids else None
    year_set = set(years) if years else None

    rows = []
    for c in _catches:
        if c["group"] not in group_set:
            continue
        if sid_set is not None and c["species_id"] not in sid_set:
            continue
        if year_set is not None and c["year"] not in year_set:
            continue
        rows.append(c)

    gidx = {g: i for i, g in enumerate(GROUP_ORDER)}
    species_idx: dict[str, int] = {}
    species_list: list[str] = []
    points = []
    minlat = minlon = 1e9
    maxlat = maxlon = -1e9
    for c in rows:
        sid = c["species_id"]
        si = species_idx.get(sid)
        if si is None:
            si = len(species_list)
            species_idx[sid] = si
            species_list.append(c["species_name"])
        lat, lon = c["lat"], c["lon"]
        points.append([
            lat, lon, gidx[c["group"]],
            c["length"], c["temp"], c["doy"], si,
            c["et_label"],
        ])
        minlat, maxlat = min(minlat, lat), max(maxlat, lat)
        minlon, maxlon = min(minlon, lon), max(maxlon, lon)

    frames, labels = _frames_for_grain(grain if grain in GRAINS else "season")

    return {
        "points": points,
        "frames": frames,
        "labels": labels,
        "groups": get_groups_meta(),
        "species": species_list,
        "bounds": [[minlat, minlon], [maxlat, maxlon]] if points else None,
        "count": len(points),
        "grain": grain,
    }
