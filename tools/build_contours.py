"""Build the /sst depth contours (assets/sst/contours.json.gz) from NOAA ETOPO 2022.

Off-Render, build-only: run it on a Mac or a GitHub runner (NOAA ERDDAP is reachable there), commit the
asset, and the app serves it as a static file (app.py /api/sst/contours.json, Content-Encoding gzip). The map
draws it above the SST and below the spots (assets/sstlook.js).

    .venv/bin/python -m pip install -r requirements-tools.txt     # netCDF4 (xarray reads the download)
    .venv/bin/python tools/build_contours.py                        # fetch (cached in build/etopo), build, write
    .venv/bin/python tools/build_contours.py --geojson /tmp/c.geojson   # also write plain GeoJSON to inspect
    .venv/bin/python tools/build_contours.py --check                # the asset's sizes and the Hudson Canyon head

Source: ETOPO 2022 v1, 15 arc-second (~460 m) bedrock/ice-surface elevation, NOAA NCEI, from the CoastWatch
ERDDAP dataset ETOPO_2022_v1_15s (variable z, metres, negative below sea level). License: free to use and
redistribute, not for legal use.

Steps (each tuned by eye at zoom 7 and at zoom 9 to 11 over Hudson Canyon and the Gulf of Maine ledges):
  1. fetch the coverage box (37.8 to 44.0 N, 76.0 to 64.8 W) plus a small margin, once (build/etopo/*.nc);
  2. keep only the open ocean: below sea level and connected to the deep water, so lakes whose beds lie below
     sea level (Seneca, Champlain) get no lines;
  3. smooth a little (Gaussian, SIGMA cells per level: less for the deep lines so the canyon walls stay crisp,
     more for 30 and 40 fathoms, where the flat shelf makes the raw lines wander cell by cell);
  4. contour at 30, 40 and 100 fathoms and 500, 1000 and 2000 m (contourpy), clip to the coverage box;
  5. simplify (Douglas-Peucker, TOL_M metres in a local equal-scale plane: well under a screen pixel at zoom 9
     and about 1.5 px at zoom 11) and drop pieces shorter than MIN_KM;
  6. quantize to 1e-4 degree (11 m) and delta-encode each piece.

Asset format ("GOCONT1", gzip-compressed JSON):
  {"format": "GOCONT1", "q": 10000, "bbox": [S, W, N, E], "source": ..., "built": ISO,
   "levels": [{"id": "30fa", "depth_m": 54.864, "label": "30 fa", "unit": "fa", "width": 0.7, "alpha": 0.7,
               "minzoom": 7, "lines": [[len_m, lon0*q, lat0*q, dlon1, dlat1, dlon2, dlat2, ...], ...]}, ...],
   "checks": {"hudson_head": [lat, lon], ...}}
  A piece is its length in metres, then its first vertex in 1e-4 degree integers, then integer steps to each
  next vertex. Closed pieces end on their first vertex.
"""

import argparse
import gzip
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "sst" / "contours.json.gz"
CACHE = ROOT / "build" / "etopo"

# The SST coverage box (sprint 2): past Oceanographer Canyon to the east, the Gulf Stream to the south.
S, N, W, E = 37.8, 44.0, -76.0, -64.8
MARGIN = 0.1                       # fetched beyond the box so smoothing and contouring have neighbours

ERDDAP = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s.nc"
HEADERS = {"User-Agent": "Mozilla/5.0 (GotOne Labs build_contours; +https://labs.gotoneapp.com)"}

FA = 1.8288
# id, depth (m, positive down), label, unit, line width (px), opacity, minimum zoom, smoothing (cells), min km
LEVELS = [
    ("30fa", 30 * FA, "30 fa", "fa", 0.7, 0.70, 7, 1.5, 3.0),
    ("40fa", 40 * FA, "40 fa", "fa", 0.7, 0.70, 7, 1.5, 3.0),
    ("100fa", 100 * FA, "100 fa", "fa", 1.3, 0.95, 5, 1.0, 2.0),
    ("500m", 500.0, "500 m", "m", 0.9, 0.82, 7, 0.9, 2.0),
    ("1000m", 1000.0, "1000 m", "m", 0.9, 0.82, 6, 0.9, 2.0),
    ("2000m", 2000.0, "2000 m", "m", 0.9, 0.82, 6, 0.9, 2.0),
]
TOL_M = 35.0                       # simplification tolerance, metres
Q = 10000                          # 1e-4 degree
LAT0 = 40.9                        # middle of the box, for the equal-scale plane
KX = math.cos(math.radians(LAT0))


def log(*a):
    print(*a, flush=True)


# ----------------------------------------------------------------------------------------------- fetch --
def fetch(force=False):
    """Download the box once; later runs reuse build/etopo/*.nc."""
    import requests

    s, n, w, e = (round(v, 3) for v in (S - MARGIN, N + MARGIN, W - MARGIN, E + MARGIN))
    name = f"etopo2022_15s_{s:.2f}_{n:.2f}_{w:.2f}_{e:.2f}.nc".replace("-", "m")
    path = CACHE / name
    if path.is_file() and path.stat().st_size > 1_000_000 and not force:
        return path
    CACHE.mkdir(parents=True, exist_ok=True)
    url = f"{ERDDAP}?z%5B({s}):1:({n})%5D%5B({w}):1:({e})%5D"
    for attempt in range(6):
        try:
            log(f"fetching ETOPO 2022 15s {s} to {n} N, {w} to {e} E (attempt {attempt + 1})")
            t0 = time.time()
            with requests.get(url, headers=HEADERS, timeout=(10, 120), stream=True) as r:
                if r.status_code in (429, 500, 502, 503, 504):
                    raise RuntimeError(f"HTTP {r.status_code}")
                r.raise_for_status()
                tmp = path.with_suffix(".part")
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            tmp.rename(path)
            log(f"  {path.stat().st_size / 1e6:.1f} MB in {time.time() - t0:.0f} s")
            return path
        except Exception as ex:  # noqa: BLE001 - retried, NOAA 503s are normal
            wait = 5 * (attempt + 1)
            log(f"  failed: {ex}; retrying in {wait} s")
            time.sleep(wait)
    sys.exit("ETOPO fetch failed")


def load(path):
    import xarray as xr

    with xr.open_dataset(path) as ds:
        z = ds["z"].values.astype("float32")
        lat = ds["latitude"].values.astype("float64")
        lon = ds["longitude"].values.astype("float64")
    if lat[0] > lat[-1]:
        z, lat = z[::-1], lat[::-1]
    return lat, lon, z


# -------------------------------------------------------------------------------------------- ocean only --
def ocean_depth(lat, lon, z):
    """Depth in metres (positive down) over the open ocean; land, and water not connected to the deep ocean
    (lakes with beds below sea level), set to -5 (5 m above sea level) so no line can form there."""
    from scipy import ndimage

    wet = z < 0
    labels, _ = ndimage.label(wet)
    # the deep water in the southeast corner of the box is open ocean
    i = int(np.searchsorted(lat, S + 0.2))
    j = int(np.searchsorted(lon, E - 0.2))
    ocean = labels == labels[i, j]
    depth = np.where(ocean, -z, -5.0).astype("float64")
    return depth


# ---------------------------------------------------------------------------------------------- contours --
def to_plane(lon, lat):
    """Local equal-scale plane in metres (for simplifying and measuring)."""
    return (np.asarray(lon) - W) * 111_320.0 * KX, (np.asarray(lat) - S) * 110_574.0


def from_plane(x, y):
    return np.asarray(x) / (111_320.0 * KX) + W, np.asarray(y) / 110_574.0 + S


def line_len_m(xy):
    d = np.diff(xy, axis=0)
    return float(np.hypot(d[:, 0], d[:, 1]).sum())


def build_level(lat, lon, depth, level_m, sigma, min_km):
    import contourpy
    from scipy.ndimage import gaussian_filter
    from shapely.geometry import LineString, box

    f = gaussian_filter(depth, sigma) if sigma > 0 else depth
    gen = contourpy.contour_generator(lon, lat, f, line_type="Separate")
    clip = box(*to_plane(W, S), *to_plane(E, N))
    pieces = []
    for ln in gen.lines(level_m):
        if len(ln) < 2:
            continue
        x, y = to_plane(ln[:, 0], ln[:, 1])
        g = LineString(np.c_[x, y])
        if not g.intersects(clip):
            continue
        g = g.intersection(clip) if not clip.contains(g) else g
        parts = getattr(g, "geoms", [g])
        for p in parts:
            if p.geom_type != "LineString" or p.is_empty:
                continue
            p = p.simplify(TOL_M, preserve_topology=False)
            xy = np.asarray(p.coords)
            if len(xy) < 2:
                continue
            L = line_len_m(xy)
            if L < min_km * 1000:
                continue
            lo, la = from_plane(xy[:, 0], xy[:, 1])
            pieces.append((L, lo, la))
    pieces.sort(key=lambda t: -t[0])
    return pieces


def encode(pieces):
    out = []
    nverts = 0
    for L, lo, la in pieces:
        qx = np.round(lo * Q).astype(np.int64)
        qy = np.round(la * Q).astype(np.int64)
        keep = np.r_[True, (np.diff(qx) != 0) | (np.diff(qy) != 0)]     # drop repeats after quantizing
        qx, qy = qx[keep], qy[keep]
        if len(qx) < 2:
            continue
        d = np.empty(2 * len(qx), dtype=np.int64)
        d[0], d[1] = qx[0], qy[0]
        d[2::2] = np.diff(qx)
        d[3::2] = np.diff(qy)
        out.append([int(round(L))] + d.tolist())
        nverts += len(qx)
    return out, nverts


def decode(piece):
    """[len_m, x0, y0, dx, dy, ...] -> (lon array, lat array). Mirrors assets/sstlook.js."""
    a = np.asarray(piece[1:], dtype=np.int64)
    return np.cumsum(a[0::2]) / Q, np.cumsum(a[1::2]) / Q


# ----------------------------------------------------------------------------------- Hudson Canyon head --
def hudson_head(lat, lon, depth):
    """Where Hudson Canyon reaches furthest inshore: the most landward point (along the shelf-normal,
    bearing 325 degrees) of the 300 m isobath inside the canyon's notch in the shelf edge, on the raw
    (unsmoothed) 15 arc-second grid. Also the axis: the deepest cell on cross-sections near the head."""
    import contourpy

    m = (lat > 39.3) & (lat < 39.95)
    k = (lon > -72.9) & (lon < -72.0)
    la, lo, d = lat[m], lon[k], depth[np.ix_(m, k)]
    gen = contourpy.contour_generator(lo, la, d, line_type="Separate")
    th = math.radians(325.0)
    ux, uy = math.sin(th), math.cos(th)                 # landward unit vector (east, north)
    best = None
    for ln in gen.lines(300.0):
        x, y = to_plane(ln[:, 0], ln[:, 1])
        s = x * ux + y * uy
        i = int(np.argmax(s))
        if best is None or s[i] > best[0]:
            best = (s[i], float(ln[i, 1]), float(ln[i, 0]))
    # how far the canyon floor sits below the shelf to either side, at the head
    hl, hn = best[1], best[2]
    i = int(np.argmin(np.abs(la - hl)))
    j = int(np.argmin(np.abs(lo - hn)))
    return {"lat": round(hl, 4), "lon": round(hn, 4), "depth_m": round(float(d[i, j]), 1)}


# ------------------------------------------------------------------------------------------------- main --
def build(args):
    path = fetch(args.force_fetch)
    lat, lon, z = load(path)
    log(f"grid {z.shape[0]} x {z.shape[1]}, {lat[0]:.3f} to {lat[-1]:.3f} N, {lon[0]:.3f} to {lon[-1]:.3f} E")
    depth = ocean_depth(lat, lon, z)
    head = hudson_head(lat, lon, depth)
    log(f"Hudson Canyon head (300 m isobath furthest inshore): {head}")

    levels = []
    total_verts = 0
    for lid, dm, label, unit, width, alpha, minzoom, sigma, min_km in LEVELS:
        t0 = time.time()
        pieces = build_level(lat, lon, depth, dm, sigma, min_km)
        lines, nv = encode(pieces)
        total_verts += nv
        km = sum(p[0] for p in pieces) / 1000
        log(f"  {label:>7}: {len(lines):4d} pieces, {nv:6d} vertices, {km:7.0f} km  ({time.time() - t0:.1f} s)")
        levels.append({"id": lid, "depth_m": round(dm, 3), "label": label, "unit": unit, "width": width,
                       "alpha": alpha, "minzoom": minzoom, "lines": lines})

    doc = {
        "format": "GOCONT1",
        "q": Q,
        "bbox": [S, W, N, E],
        "source": ("NOAA NCEI ETOPO 2022 v1, 15 arc-second (ETOPO_2022_v1_15s via NOAA CoastWatch ERDDAP); "
                   f"Gaussian smoothing per level, simplified to {TOL_M:.0f} m"),
        "built": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "checks": {"hudson_head": [head["lat"], head["lon"]], "hudson_head_depth_m": head["depth_m"]},
        "levels": levels,
    }
    raw = json.dumps(doc, separators=(",", ":")).encode()
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gz)
    log(f"wrote {OUT.relative_to(ROOT)}: {total_verts} vertices, {len(raw) / 1024:.0f} KB JSON, "
        f"{len(gz) / 1024:.0f} KB gzipped, sha1 {hashlib.sha1(gz).hexdigest()[:10]}")
    if args.geojson:
        write_geojson(doc, Path(args.geojson))


def write_geojson(doc, path):
    feats = []
    for lv in doc["levels"]:
        coords = []
        for piece in lv["lines"]:
            lo, la = decode(piece)
            coords.append([[round(a, 4), round(b, 4)] for a, b in zip(lo, la)])
        props = {k: v for k, v in lv.items() if k != "lines"}
        feats.append({"type": "Feature", "properties": props,
                      "geometry": {"type": "MultiLineString", "coordinates": coords}})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    log(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


def check():
    gz = OUT.read_bytes()
    doc = json.loads(gzip.decompress(gz))
    log(f"{OUT.relative_to(ROOT)}: {len(gz) / 1024:.0f} KB gzipped, {len(gzip.decompress(gz)) / 1024:.0f} KB JSON, "
        f"built {doc['built']}")
    for lv in doc["levels"]:
        nv = sum((len(p) - 1) // 2 for p in lv["lines"])
        log(f"  {lv['label']:>7}: {len(lv['lines'])} pieces, {nv} vertices")
    log(f"  Hudson Canyon head: {doc['checks']['hudson_head']} ({doc['checks']['hudson_head_depth_m']} m)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--geojson", help="also write plain GeoJSON here (to inspect in QGIS or geojson.io)")
    ap.add_argument("--force-fetch", action="store_true", help="download ETOPO again")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--check", action="store_true", help="print the committed asset's sizes and checks")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.fetch_only:
        return fetch(args.force_fetch)
    build(args)


if __name__ == "__main__":
    main()
