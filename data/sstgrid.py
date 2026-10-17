"""The week's SST grid as the cache stores it and the browser draws it (sprint 2, format "GOSST1").

One file per week (no more "adaptive" / "locked" pair: the color scale is chosen in the browser now). Per day, the
temperature at 0.1 F on MUR's 0.01 degree grid over the coverage box (config.json) and one water/land mask for the
week. assets/sstengine.js decodes the same bytes; tools/test_sst_grid.py checks the round trip.

No cloud layer: MUR's analysis_error was the planned source of a "this is an estimate" haze (review D3), but over the
whole box it is 0.36 to 0.40 C on every date checked (2005 to 2026, 4 or 5 distinct values), so it can't tell a
cloudy week from a clear one, and the haze was dropped (Luyen, Oct 16 2026). A real signal (per-pixel L3 infrared
observation counts, for instance) would need its own fetch; bytes 40 to 63 of the header are reserved for it.

Binary layout (little-endian), gzip-compressed as a whole. /api/sst/week/<end>.bin serves the gzip bytes as they are
with `Content-Encoding: gzip`, so every browser inflates them natively; the cache entry keeps them base64-encoded.

  off  type        field
  0    6s          magic b"GOSST1"
  6    H           version (1)
  8    H           nx        columns, west -> east
  10   H           ny        rows, north -> south
  12   H           nd        days
  14   H           flags     bit 1: whole-degree data (entries saved before sprint 2); bit 0 reserved
  16   f           lat_n     centre latitude of row 0
  20   f           lat_s     centre latitude of row ny - 1
  24   f           lon_w     centre longitude of column 0
  28   f           lon_e     centre longitude of column nx - 1
  32   f           base      F of code 0 (28.0)
  36   f           step      F per code (0.1)
  40   24x         reserved (zero)
  64   u8[ny*nx]   mask      0 water, 1 land, 2 no data (outside an older, smaller coverage)
  then per day:
       u32         nesc      escaped residuals that follow the day's bytes
       u8[ny*nx]   residual bytes, row by row
       i16[nesc]   escaped residuals, in order

Codes: code = round((F - base) / step), 0..MAX_CODE. A cell's value is predicted by its left neighbour (column 0: the
column-0 value of the row above; the first cell: 0) and the residual is zigzag-coded in one byte: 0..253 inline
(residual -127..126), 254 = water with no value on this day, 255 = escaped (the next i16 of the day's escape list).
Cells that aren't water carry the value forward (residual 0), so land costs almost nothing. Measured on the week of
Oct 9 to 15, 2026 over the whole box: 588 KB for the 7 days at gzip -9 (the seeder), 641 KB at -6 (built on Render);
plain gzip of the uint16 codes: 1002 KB.
"""

import base64
import gzip
import io
import math
import struct
from typing import Dict, List, Optional, Tuple

import numpy as np

MAGIC = b"GOSST1"
VERSION = 1
GRID_FORMAT = 1                 # cache entries carry "grid_format": GRID_FORMAT
HEADER_FMT = "<6sHHHHHffffff24x"
HEADER_SIZE = struct.calcsize(HEADER_FMT)   # 64
BASE_F = 28.0                   # code 0 (sea water freezes near 28.4 F)
STEP_F = 0.1
MAX_CODE = 4000                 # 428 F: keeps every residual inside an i16
FLAG_WHOLE = 2
WATER, LAND, NODATA = 0, 1, 2
ESC, MISS = 255, 254
AUTO_MIN_SPAN = 8               # F, design-notes "Auto": a smaller span exaggerates uniform winter water
LOCKED_RANGE = (30.0, 90.0)     # the old "Same colors every week"


# ---------------------------------------------------------------- codes --

def quantize(arrF: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """F (NaN = no data) -> (int32 codes, valid bool)."""
    a = np.asarray(arrF, dtype=np.float64)
    valid = np.isfinite(a)
    codes = np.zeros(a.shape, dtype=np.int32)
    codes[valid] = np.clip(np.rint((a[valid] - BASE_F) / STEP_F), 0, MAX_CODE).astype(np.int32)
    return codes, valid


def codes_to_f(codes: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """int codes -> float32 F (NaN where not valid), rounded to 0.1 F."""
    out = np.full(codes.shape, np.nan, dtype=np.float32)
    out[valid] = np.round(BASE_F + codes[valid] * STEP_F, 1)
    return out


def _carry(codes: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Codes with every invalid cell set to its predictor (left neighbour; column 0: the row above), so its residual
    is 0."""
    ny, nx = codes.shape
    v0 = valid[:, 0]
    idx = np.where(v0, np.arange(ny), -1)
    np.maximum.accumulate(idx, out=idx)
    col0 = np.where(idx >= 0, codes[np.maximum(idx, 0), 0], 0)
    arr = codes.copy()
    arr[:, 0] = col0
    val = valid.copy()
    val[:, 0] = True
    jdx = np.where(val, np.arange(nx)[None, :], 0)
    np.maximum.accumulate(jdx, axis=1, out=jdx)
    return arr[np.arange(ny)[:, None], jdx]


def encode_day(codes: np.ndarray, valid: np.ndarray, water: np.ndarray) -> bytes:
    """One day's residual block: u32 nesc + u8[ny*nx] + i16[nesc]."""
    filled = _carry(codes.astype(np.int32), valid)
    pred = np.empty_like(filled)
    pred[:, 1:] = filled[:, :-1]
    pred[1:, 0] = filled[:-1, 0]
    pred[0, 0] = 0
    r = (filled - pred).astype(np.int32).ravel()
    zz = (r << 1) ^ (r >> 31)
    inline = zz <= 253
    b = np.where(inline, zz, ESC).astype(np.uint8)
    b[(water & ~valid).ravel()] = MISS            # residual is 0 there (carried)
    esc = r[~inline].astype("<i2")
    return struct.pack("<I", len(esc)) + b.tobytes() + esc.tobytes()


def decode_day(buf: memoryview, off: int, n: int, ny: int, nx: int):
    """Inverse of encode_day at buf[off:]: (codes int32 (ny, nx), missing bool (ny, nx), next offset)."""
    (nesc,) = struct.unpack_from("<I", buf, off)
    off += 4
    b = np.frombuffer(buf, dtype=np.uint8, count=n, offset=off)
    off += n
    esc = np.frombuffer(buf, dtype="<i2", count=nesc, offset=off).astype(np.int32)
    off += 2 * nesc
    bi = b.astype(np.int32)
    r = (bi >> 1) ^ -(bi & 1)
    r[b == ESC] = esc
    miss = b == MISS
    r[miss] = 0
    r = r.reshape(ny, nx)
    r[:, 0] = np.cumsum(r[:, 0])
    return np.cumsum(r, axis=1), miss.reshape(ny, nx), off


# --------------------------------------------------------------- packing --

def _regular(vals: np.ndarray, what: str) -> Tuple[float, float]:
    """(first, last) of evenly spaced centres; raises if they aren't (the browser assumes a regular grid)."""
    v = np.asarray(vals, dtype=np.float64)
    if len(v) < 2:
        raise ValueError(f"{what}: need at least 2 values")
    lin = np.linspace(v[0], v[-1], len(v))
    step = abs(v[-1] - v[0]) / (len(v) - 1)
    if np.max(np.abs(v - lin)) > max(1e-4, step * 0.02):
        raise ValueError(f"{what}: not evenly spaced")
    return float(v[0]), float(v[-1])


def pack_week(days_f: List[np.ndarray], lats: np.ndarray, lons: np.ndarray, *, nonwater: int = LAND,
              whole: Optional[bool] = None, level: int = 6) -> bytes:
    """The week as gzip-compressed GOSST1 bytes.

    days_f: per day F arrays (ny, nx), row 0 north (geo.orient_to_leaflet), NaN = no data. lats (north first) and
    lons are cell centres. Cells with no value on any day are `nonwater` in the mask (LAND for fresh data, which has
    the land mask applied; NODATA for old entries, where land and "outside the old area" can't be told apart).
    whole: data in whole degrees (entries saved before sprint 2); None = detect.
    """
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    if lats[0] < lats[-1]:
        raise ValueError("pack_week: rows must run north to south")
    ny, nx = len(lats), len(lons)
    lat_n, lat_s = _regular(lats, "lats")
    lon_w, lon_e = _regular(lons, "lons")
    qs = [quantize(d) for d in days_f]
    for c, _ in qs:
        if c.shape != (ny, nx):
            raise ValueError(f"pack_week: day shape {c.shape} != {(ny, nx)}")
    water = np.zeros((ny, nx), dtype=bool)
    for _, v in qs:
        water |= v
    if whole is None:
        whole = all(np.all(c[v] % 10 == 0) for c, v in qs if v.any())
    mask = np.where(water, WATER, nonwater).astype(np.uint8)

    flags = FLAG_WHOLE if whole else 0
    header = struct.pack(HEADER_FMT, MAGIC, VERSION, nx, ny, len(qs), flags, lat_n, lat_s, lon_w, lon_e,
                         BASE_F, STEP_F)
    parts = [header, mask.tobytes()]
    for c, v in qs:
        parts.append(encode_day(c, v, water))
    return gzip.compress(b"".join(parts), compresslevel=level, mtime=0)


def read_header(raw: bytes) -> Dict:
    if len(raw) < HEADER_SIZE:
        raise ValueError("GOSST1: shorter than its header")
    (magic, version, nx, ny, nd, flags, lat_n, lat_s, lon_w, lon_e, base, step) = struct.unpack_from(HEADER_FMT, raw)
    if magic != MAGIC:
        raise ValueError(f"GOSST1: bad magic {magic!r}")
    if version != VERSION:
        raise ValueError(f"GOSST1: unsupported version {version}")
    return {"nx": nx, "ny": ny, "nd": nd, "flags": flags, "lat_n": lat_n, "lat_s": lat_s, "lon_w": lon_w,
            "lon_e": lon_e, "base": base, "step": step, "whole": bool(flags & FLAG_WHOLE)}


def unpack_week(blob: bytes) -> Dict:
    """gzip GOSST1 bytes -> {"header", "mask", "codes": [int32 (ny, nx)], "valid": [bool], "lats", "lons"}."""
    raw = gzip.decompress(blob) if blob[:2] == b"\x1f\x8b" else bytes(blob)
    h = read_header(raw)
    ny, nx, n = h["ny"], h["nx"], h["ny"] * h["nx"]
    buf = memoryview(raw)
    off = HEADER_SIZE
    mask = np.frombuffer(buf, dtype=np.uint8, count=n, offset=off).reshape(ny, nx)
    off += n
    codes, valid = [], []
    for _ in range(h["nd"]):
        c, miss, off = decode_day(buf, off, n, ny, nx)
        codes.append(c)
        valid.append((mask == WATER) & ~miss)
    if off != len(raw):
        raise ValueError(f"GOSST1: {len(raw) - off} bytes left over")
    lats = np.linspace(h["lat_n"], h["lat_s"], ny)
    lons = np.linspace(h["lon_w"], h["lon_e"], nx)
    return {"header": h, "mask": mask, "codes": codes, "valid": valid, "lats": lats, "lons": lons}


def bounds_of(h: Dict):
    """[[south, west], [north, east]] of the grid's outer cell edges (as geo.grid_bounds)."""
    dlat = (h["lat_n"] - h["lat_s"]) / max(1, h["ny"] - 1)
    dlon = (h["lon_e"] - h["lon_w"]) / max(1, h["nx"] - 1)
    return [[h["lat_s"] - dlat / 2, h["lon_w"] - dlon / 2], [h["lat_n"] + dlat / 2, h["lon_e"] + dlon / 2]]


# ---------------------------------------------------------- statistics --

def code_histogram(codes: List[np.ndarray], valid: List[np.ndarray]) -> np.ndarray:
    """Counts per code over every valid cell of every day (the color bar's distribution, the Auto range)."""
    h = np.zeros(MAX_CODE + 1, dtype=np.int64)
    for c, v in zip(codes, valid):
        if v.any():
            h += np.bincount(c[v].ravel(), minlength=MAX_CODE + 1)[: MAX_CODE + 1]
    return h


def _rank(hist: np.ndarray, p: float) -> int:
    """The smallest code whose cumulative count reaches p of the total (assets/sstengine.js does the same)."""
    cum = np.cumsum(hist)
    return int(np.searchsorted(cum, p * cum[-1], side="left"))


def auto_range(hist: np.ndarray) -> Tuple[float, float]:
    """Auto: the 5th to 95th percentile of all water in the coverage over the week, at 0.1 F, rounded outward to whole
    degrees, at least AUTO_MIN_SPAN wide. Mirrored exactly by SSTEngine (same integer math)."""
    if hist.sum() <= 0:
        return LOCKED_RANGE
    t5 = int(round(BASE_F * 10)) + _rank(hist, 0.05)       # tenths of a degree
    t95 = int(round(BASE_F * 10)) + _rank(hist, 0.95)
    lo, hi = t5 // 10, -((-t95) // 10)
    if hi - lo < AUTO_MIN_SPAN:
        mid2 = lo + hi                                       # twice the middle, whole degrees
        lo = (mid2 - AUTO_MIN_SPAN) // 2
        hi = lo + AUTO_MIN_SPAN
    return float(lo), float(hi)


# ------------------------------------------------------- building weeks --

def build_week(days_f: List[np.ndarray], dates: List[str], lats, lons, *, meta: Dict, coverage: str,
               server: str = "", dataset_id: str = "", dataset_title: str = "", nonwater: int = LAND,
               whole=None, level: int = 6) -> Dict:
    """A cache entry (grid format) for one week. Stored as JSON (gzip) by data/cache.put_week."""
    blob = pack_week(days_f, lats, lons, nonwater=nonwater, whole=whole, level=level)
    return entry_from_blob(blob, dates, meta=meta, coverage=coverage, server=server, dataset_id=dataset_id,
                           dataset_title=dataset_title)


def entry_from_blob(blob: bytes, dates: List[str], *, meta: Dict, coverage: str, server: str = "",
                    dataset_id: str = "", dataset_title: str = "", week: Optional[Dict] = None) -> Dict:
    week = week or unpack_week(blob)
    h = week["header"]
    if len(dates) != h["nd"]:
        raise ValueError(f"{len(dates)} dates for {h['nd']} days")
    vmin, vmax = auto_range(code_histogram(week["codes"], week["valid"]))
    res_km = abs(h["lat_n"] - h["lat_s"]) / max(1, h["ny"] - 1) * 111.0
    return {
        "grid_format": GRID_FORMAT,
        "coverage": coverage,
        "dates": list(dates),
        "bounds": bounds_of(h),
        "vmin": vmin, "vmax": vmax,
        "precision": 1.0 if h["whole"] else STEP_F,
        "nx": h["nx"], "ny": h["ny"],
        "res_km": round(res_km, 3),
        "server": server, "dataset_id": dataset_id, "dataset_title": dataset_title,
        "meta": meta,
        "grid_bytes": len(blob),
        "grid": base64.b64encode(blob).decode("ascii"),
    }


def is_grid_entry(entry: Optional[Dict]) -> bool:
    return bool(entry) and entry.get("grid_format") == GRID_FORMAT and isinstance(entry.get("grid"), str)


def entry_blob(entry: Dict) -> bytes:
    return base64.b64decode(entry["grid"])


def week_arrays(blob: bytes, dates: List[str], week: Optional[Dict] = None) -> Dict:
    """Float arrays for the server's readouts (map/readout.week_temps, the measure panel): {"raw_days": [{"arrF",
    "date"}], "lats", "lons"}, float32 F with NaN where there's no value."""
    week = week or unpack_week(blob)
    days = [{"arrF": codes_to_f(c, v), "date": d} for c, v, d in zip(week["codes"], week["valid"], dates)]
    return {"raw_days": days, "lats": week["lats"], "lons": week["lons"]}


# --------------------------------------------------------- old entries --

def _npy(s) -> np.ndarray:
    if isinstance(s, str):
        return np.load(io.BytesIO(base64.b64decode(s)), allow_pickle=False)
    return np.array(s, dtype=np.float64)


def old_entry_arrays(entry: Dict) -> Dict:
    """An entry saved before sprint 2 (v1 JSON lists, v2 base64 .npy, raw-only or with PNG frames; "adaptive" or
    "locked": the arrays are the same) -> {"raw_days", "lats", "lons"} with rows north first."""
    lats = _npy(entry["lats"]).astype(np.float64)
    lons = _npy(entry["lons"]).astype(np.float64)
    days = [{"arrF": _npy(rd["arrF"]).astype(np.float32), "date": rd["date"]} for rd in entry["raw_days"]]
    if lats[0] < lats[-1]:
        lats = lats[::-1]
        for d in days:
            d["arrF"] = d["arrF"][::-1]
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        for d in days:
            d["arrF"] = d["arrF"][:, ::-1]
    return {"raw_days": days, "lats": lats, "lons": lons}


def old_entry_blob(entry: Dict, arrays: Optional[Dict] = None, level: int = 6) -> bytes:
    """GOSST1 bytes for an old entry, on its own (smaller) grid and bounds. Not written back: the seeder
    replaces old weeks with the extended coverage over time (tools/sync_cache.py --backfill)."""
    a = arrays or old_entry_arrays(entry)
    return pack_week([d["arrF"] for d in a["raw_days"]], a["lats"], a["lons"], nonwater=NODATA, level=level)


def store_payload(entry: Dict, end_iso: str, *, url: str) -> Dict:
    """The page's sst-store data for a week (no pictures: assets/sstengine.js fetches `grid.url` and draws it).

    Contract for SSTEngine.setWeek(payload): dates, meta, bounds, vmin/vmax (the Auto range), data_key, and
    grid = {url, bytes, nx, ny, precision, coverage, format}."""
    return {
        "dates": entry["dates"],
        "bounds": entry["bounds"],
        "vmin": float(entry["vmin"]),
        "vmax": float(entry["vmax"]),
        "res_km": entry.get("res_km"),
        "server": entry.get("server", ""),
        "dataset_id": entry.get("dataset_id", ""),
        "dataset_title": entry.get("dataset_title", ""),
        "meta": entry.get("meta"),
        "grid": {"url": url, "bytes": entry.get("grid_bytes"), "nx": entry.get("nx"), "ny": entry.get("ny"),
                 "precision": entry.get("precision", STEP_F), "coverage": entry.get("coverage"),
                 "format": GRID_FORMAT},
    }


def stamp(built_at: Optional[str]) -> str:
    """A short version tag for the week's URL from its meta built_at (busts the browser cache on a refresh)."""
    s = "".join(ch for ch in str(built_at or "") if ch.isdigit())
    return s[:14] or "0"


def mercator_y(lat_deg: float) -> float:
    return math.log(math.tan(math.pi / 4 + math.radians(lat_deg) / 2))


def _rect_aoi(config: Dict) -> bool:
    """The config's area is a plain lon/lat rectangle (since sprint 2: the whole coverage box), so masking to it is a
    no-op on a grid fetched for its bounding box."""
    pts = [tuple(p) for p in config.get("aoi_polygon_lonlat") or []]
    if len(pts) == 5 and pts[0] == pts[-1]:
        pts = pts[:4]
    if len(pts) != 4:
        return False
    lons = sorted({p[0] for p in pts})
    lats = sorted({p[1] for p in pts})
    return len(lons) == 2 and len(lats) == 2 and {(x, y) for x in lons for y in lats} == set(pts)


def entry_from_fetch(sst: Dict, config: Dict, *, level: int = 6) -> Dict:
    """The weekly cache entry for a week get_sst_multiday() returned (the app's reader fetch, /api/precache and the
    seeder all build it here): rows north first, land masked (Natural Earth 10m, data/geo), 0.1 F codes, the Auto
    range, and meta."""
    from data import sstmeta
    from data.geo import mask_aoi_rasterized, mask_land_rasterized, orient_to_leaflet

    lats_raw, lons_raw = sst["lats"], sst["lons"]
    rect = _rect_aoi(config)
    days, dates = [], []
    lats = lons = None
    for d in sst["days"]:
        arr, lats, lons = orient_to_leaflet(d["arrF"], np.asarray(lats_raw).copy(), np.asarray(lons_raw).copy())
        if not rect:
            arr = mask_aoi_rasterized(arr, lats, lons, config)
        arr = mask_land_rasterized(arr, lats, lons)
        days.append(np.asarray(arr, dtype=np.float32))
        dates.append(d["date"])
    return build_week(days, dates, lats, lons, meta=sstmeta.meta_from_fetch(sst),
                      coverage=config.get("coverage_id") or "aoi", server=sst.get("server", ""),
                      dataset_id=sst.get("dataset_id", ""), dataset_title=sst.get("dataset_title", ""), level=level)
