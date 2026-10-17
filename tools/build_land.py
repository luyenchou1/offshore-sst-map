"""Build the /sst coastline (assets/sst/land/*.json.gz) from OpenStreetMap land polygons.

Off-Render, build-only. Download once, build, commit the assets; the app serves them as static files
(app.py /api/sst/land/<name>.json, Content-Encoding gzip, map/land.py) and the browser draws them on the SST canvas
(assets/sstland.js + assets/sstengine.js): the SST is erased on land along this coastline and a thin rim of the
basemap's land color covers the seam, so the coast is a smooth vector line at every zoom.

    .venv/bin/python -m pip install -r requirements-tools.txt     # shapely + pyogrio read the shapefile
    .venv/bin/python tools/build_land.py                            # download (once, build/land), build, write
    .venv/bin/python tools/build_land.py --check                    # the committed assets' sizes and vertices
    .venv/bin/python tools/build_land.py --geojson /tmp/land1.geojson --level 1   # one level as GeoJSON

Source: OpenStreetMap land polygons, split, WGS84 (https://osmdata.openstreetmap.de/data/land-polygons.html),
built from OSM's natural=coastline every day by osmdata.openstreetmap.de. 928 MB zip for the world (Oct 2026),
kept in build/land (gitignored); a second run downloads nothing. License: ODbL 1.0, (c) OpenStreetMap
contributors. The map's attribution already credits "OpenStreetMap contributors" (the Esri basemap line); the
assets are a derivative database, also ODbL (assets/sst/land/README.txt).

Why OSM (compared over Esri tiles at Hampton Bays z12, Montauk z11, Casco Bay z11 and the Chesapeake mouth z10,
Oct 17 2026; docs/coast-progress.md): OSM's coastline sits on the basemap's own water edge within about a pixel at
zoom 12, tidal creeks included; GSHHG full resolution (WVS, 1990s) is 100 to 200 m off in places and stair-stepped
at zoom 12; Natural Earth 10m is kilometres off (fine only below zoom 7, and it is what MUR's mask already uses);
NOAA's CUSP shoreline is lines only and US only (the box reaches Nova Scotia), so it would need polygon assembly.
OSM's cost is the download, once; the assets themselves stay small (below).

Steps:
  1. read the polygons that touch the box (37.8 to 44.0 N, 76.0 to 64.8 W, plus MARGIN) and union them;
  2. per level, simplify (topology-preserving Douglas-Peucker in a local equal-scale plane, TOL_M metres: about a
     third of a screen pixel at the level's top zoom) and drop polygons and holes smaller than MIN_M2;
  3. cut into 1-degree tiles that overlap by OVERLAP (filled as one path in the browser, so tile edges never show)
     and quantize to 1/Q degree; rings are oriented (outer counter-clockwise, holes clockwise: nonzero fill).

Levels (zoom ranges are what the browser uses each for):
  0  zoom 5 to 9    ~90 m    one file with every tile, loaded with the page (budget 150 KB gzipped)
  1  zoom 10 to 11  ~20 m    one file per tile, loaded when a view needs it
  2  zoom 12        ~7 m     one file per tile, loaded when a view needs it (levels 1 + 2 budget 1.5 MB)

Asset format ("GOLAND1", gzip-compressed JSON), assets/sst/land/land-0.json.gz and land-<level>-<tile>.json.gz:
  {"format": "GOLAND1", "level": 0, "q": 10000, "zoom": [5, 9], "tol_m": 90, "box": [W, S, E, N],
   "source": ..., "osm_date": ISO, "built": ISO,
   "tiles": [{"id": "w73n40.5", "clip": [x0, y0, x1, y1], "rings": [[x, y, dx, dy, dx, dy, ...], ...],
              "islets": [[...], ...]}, ...],
   "index": {"1": {"zoom": [10, 11], "tiles": {"w73n40": [sha1 prefix, bytes], ...}}, "2": {...}}}   (level 0 only)
  Tile "w73n40" spans 73 to 72 W, 40 to 41 N. A ring is its first vertex in 1/q degree integers (lon, lat), then
  integer steps to each next vertex; it closes on itself (the first vertex isn't repeated). "clip" is the tile's
  cut rectangle in the same units: ring edges lying on it are cuts, not coastline (the browser doesn't draw the rim
  there).
"""

import argparse
import gzip
import hashlib
import json
import math
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "assets" / "sst" / "land"
WORK = ROOT / "build" / "land"
SRC_URL = "https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip"
SRC_ZIP = WORK / "land-polygons-split-4326.zip"
SRC_SHP = "land-polygons-split-4326/land_polygons.shp"
HEADERS = {"User-Agent": "Mozilla/5.0 (GotOne Labs build_land; +https://labs.gotoneapp.com)"}

# The SST coverage box (sprint 2). The week's grid reaches half a cell (0.005 deg) beyond it.
S, N, W, E = 37.8, 44.0, -76.0, -64.8
MARGIN = 0.02                      # land kept beyond the box, so the grid's outer cells are covered
READ_MARGIN = 0.1                  # polygons read beyond that (the split polygons overlap slightly)
TILE = 1                           # degrees: one file per tile for levels 1 to 3
OVERLAP = 0.0002                   # degrees each piece reaches into its neighbours (~20 m)
LAT0 = 40.9                        # middle of the box, for the equal-scale plane
KX = math.cos(math.radians(LAT0))
M_PER_DEG = 111320.0

# level: (zoom range, simplification tolerance in metres, smallest polygon or hole kept in m2, 1/q degree,
#         piece size in degrees). The tolerance is about a third of a screen pixel at the level's zoom (a pixel is
#         115 m at zoom 10, 58 m at 11, 29 m at 12 at 41 N); pieces are what a view culls by.
LEVELS = {
    0: ((5, 9), 90.0, 12000.0, 10000, 0.5),
    1: ((10, 10), 45.0, 3000.0, 20000, 0.25),
    2: ((11, 11), 20.0, 800.0, 20000, 0.25),
    3: ((12, 12), 7.0, 150.0, 50000, 0.25),
}
# Islands smaller than this go in a piece's "islets" list: the browser paints them in the basemap's land color
# instead of clearing the SST over them, because the basemap leaves many small islands and ledges out (Casco Bay,
# zoom 12), and a cleared islet would show the basemap's water inside its outline.
ISLET_M2 = 1.0e6
BUDGET_L0 = 150 * 1024
BUDGET_DETAIL = 1536 * 1024
SOURCE = ("OpenStreetMap land polygons (osmdata.openstreetmap.de, from natural=coastline), "
          "(c) OpenStreetMap contributors, ODbL 1.0")


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------------------------- download --
def download(force=False):
    """The world's split land polygons, once (928 MB in Oct 2026); later runs reuse build/land."""
    if SRC_ZIP.is_file() and SRC_ZIP.stat().st_size > 100_000_000 and not force:
        return SRC_ZIP
    import requests

    WORK.mkdir(parents=True, exist_ok=True)
    tmp = SRC_ZIP.with_suffix(".part")
    log(f"downloading {SRC_URL} (about 0.9 GB) into {WORK.relative_to(ROOT)}")
    t0 = time.time()
    with requests.get(SRC_URL, headers=HEADERS, timeout=(10, 120), stream=True) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        with open(tmp, "wb") as fh:
            for chunk in r.iter_content(4 << 20):
                fh.write(chunk)
                got += len(chunk)
                if total and got % (100 << 20) < (4 << 20):
                    log(f"  {got / 1e6:.0f} of {total / 1e6:.0f} MB")
    tmp.rename(SRC_ZIP)
    log(f"  {SRC_ZIP.stat().st_size / 1e6:.0f} MB in {time.time() - t0:.0f} s")
    return SRC_ZIP


def osm_date(path):
    """The data date osmdata.openstreetmap.de writes in the zip's README ("Date of the data used is ...")."""
    try:
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith("README.txt"))
            for line in z.read(name).decode("utf-8", "replace").splitlines():
                if "Date of the data used is" in line:
                    return line.split("is", 1)[1].strip()
    except (OSError, StopIteration, zipfile.BadZipFile):
        pass
    return None


def read_land(path):
    """Every land polygon touching the box (plus margins), unioned and clipped to the box + MARGIN."""
    import pyogrio
    import shapely
    from shapely.geometry import box

    t0 = time.time()
    bb = (W - READ_MARGIN, S - READ_MARGIN, E + READ_MARGIN, N + READ_MARGIN)
    df = pyogrio.read_dataframe(f"zip://{path}!{SRC_SHP}", bbox=bb)
    geom = shapely.union_all(shapely.make_valid(df.geometry.values))
    geom = shapely.intersection(geom, box(W - MARGIN, S - MARGIN, E + MARGIN, N + MARGIN))
    log(f"read {len(df)} OSM land polygons, {shapely.get_num_coordinates(geom)} vertices in the box "
        f"({time.time() - t0:.1f} s)")
    return geom


# ------------------------------------------------------------------------------------------- simplify --
def to_plane(geom):
    from shapely import affinity
    return affinity.scale(geom, xfact=KX, yfact=1.0, origin=(0, 0))


def from_plane(geom):
    from shapely import affinity
    return affinity.scale(geom, xfact=1.0 / KX, yfact=1.0, origin=(0, 0))


def area_m2(poly):
    return poly.area * M_PER_DEG * M_PER_DEG * KX


def simplify_level(geom, tol_m, min_m2):
    """Topology-preserving simplification in the equal-scale plane, small polygons and holes dropped, oriented:
    (land, islets), two MultiPolygons (islets: islands under ISLET_M2)."""
    import shapely
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient

    s = from_plane(shapely.simplify(to_plane(geom), tol_m / M_PER_DEG, preserve_topology=True))
    land, islets = [], []
    for p in getattr(s, "geoms", [s]):
        if p.is_empty or p.geom_type != "Polygon" or area_m2(p) < min_m2:
            continue
        holes = [h for h in p.interiors if area_m2(Polygon(h)) >= min_m2]
        (islets if area_m2(p) < ISLET_M2 else land).append(orient(Polygon(p.exterior, holes), 1.0))
    mp = shapely.MultiPolygon
    return (mp(land) if land else mp()), (mp(islets) if islets else mp())


# --------------------------------------------------------------------------------------------- tiles --
def tile_id(x, y):
    return f"w{-x:02d}n{y:02d}" if x < 0 else f"e{x:02d}n{y:02d}"


def tile_cells():
    for y in range(math.floor(S - MARGIN), math.ceil(N + MARGIN)):
        for x in range(math.floor(W - MARGIN), math.ceil(E + MARGIN)):
            yield x, y


def clip_rect(x, y, size):
    """A piece's cut rectangle: its square (size degrees from x, y) plus OVERLAP, inside the box + MARGIN."""
    return (max(x - OVERLAP, W - MARGIN), max(y - OVERLAP, S - MARGIN),
            min(x + size + OVERLAP, E + MARGIN), min(y + size + OVERLAP, N + MARGIN))


def encode_ring(coords, q):
    """(lon, lat) ring -> [x0, y0, dx, dy, ...] in 1/q degree, repeats and the closing vertex dropped; None if it
    collapses."""
    a = np.round(np.asarray(coords, dtype=np.float64) * q).astype(np.int64)
    if len(a) and (a[0] == a[-1]).all():
        a = a[:-1]
    if len(a) < 3:
        return None
    keep = np.r_[True, np.any(a[1:] != a[:-1], axis=1)]
    a = a[keep]
    if len(a) >= 2 and (a[0] == a[-1]).all():
        a = a[:-1]
    if len(a) < 3:
        return None
    x, y = a[:, 0].astype(np.float64), a[:, 1].astype(np.float64)
    if abs(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) < 1:      # zero area after quantizing
        return None
    d = np.empty(2 * len(a), dtype=np.int64)
    d[0], d[1] = a[0]
    d[2::2] = np.diff(a[:, 0])
    d[3::2] = np.diff(a[:, 1])
    return d.tolist()


def decode_ring(r, q):
    """[x0, y0, dx, dy, ...] -> list of (lon, lat). Mirrors assets/sstland.js."""
    a = np.asarray(r, dtype=np.int64)
    return list(zip(np.cumsum(a[0::2]) / q, np.cumsum(a[1::2]) / q))


def encode_part(part, q):
    from shapely.geometry import Polygon
    from shapely.geometry.polygon import orient

    rings = []
    for p in getattr(part, "geoms", [part]):
        if p.is_empty or p.geom_type != "Polygon":
            continue
        p = orient(Polygon(p.exterior, p.interiors), 1.0)
        for ring in [p.exterior] + list(p.interiors):
            enc = encode_ring(ring.coords, q)
            if enc:
                rings.append(enc)
    return rings


def build_pieces(geoms, q, x, y, size):
    """The pieces (size degrees square) of 1-degree tile (x, y): [{"id", "clip", "rings", "islets"}], empty ones
    left out. geoms = (land, islets) from simplify_level."""
    import shapely
    from shapely.geometry import box

    out = []
    n = int(round(TILE / size))
    tile = [shapely.intersection(g, box(*clip_rect(x, y, TILE))) for g in geoms]
    if all(t.is_empty for t in tile):
        return out
    for j in range(n):
        for i in range(n):
            px, py = x + i * size, y + j * size
            rect = clip_rect(px, py, size)
            if rect[2] <= rect[0] or rect[3] <= rect[1]:
                continue
            rings, islets = (encode_part(shapely.intersection(t, box(*rect)), q) if not t.is_empty else []
                             for t in tile)
            if rings or islets:
                piece = {"id": f"{tile_id(x, y)}.{j * n + i}", "clip": [int(round(v * q)) for v in rect],
                         "rings": rings}
                if islets:
                    piece["islets"] = islets
                out.append(piece)
    return out


def nverts(tiles):
    return sum(len(r) // 2 for t in tiles for r in t["rings"] + t.get("islets", []))


def write_gz(path, doc):
    raw = json.dumps(doc, separators=(",", ":")).encode()
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    path.write_bytes(gz)
    return len(raw), gz


# --------------------------------------------------------------------------------------------- build --
def build(args):
    src = download(args.force_download)
    date = osm_date(src)
    geom = read_land(src)
    built = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("land-*.json.gz"):
        old.unlink()
    base = {"format": "GOLAND1", "box": [W, S, E, N], "source": SOURCE, "osm_date": date, "built": built}
    index = {}
    detail_bytes = 0
    level0 = None
    for level in sorted(LEVELS, reverse=True):
        (z0, z1), tol, min_m2, q, size = LEVELS[level]
        t0 = time.time()
        simple = simplify_level(geom, tol, min_m2)
        head = {**base, "level": level, "q": q, "zoom": [z0, z1], "tol_m": tol, "piece_deg": size}
        pieces_all = []
        entries = {}
        sizes = []
        for x, y in tile_cells():
            pieces = build_pieces(simple, q, x, y, size)
            if not pieces:
                continue
            pieces_all.extend(pieces)
            if level == 0:
                continue
            _, gz = write_gz(OUT_DIR / f"land-{level}-{tile_id(x, y)}.json.gz", {**head, "tiles": pieces})
            entries[tile_id(x, y)] = [hashlib.sha1(gz).hexdigest()[:10], len(gz)]
            sizes.append(len(gz))
        if level == 0:
            level0 = (head, pieces_all)
            continue
        detail_bytes += sum(sizes)
        index[str(level)] = {"zoom": [z0, z1], "tol_m": tol, "tiles": entries}
        log(f"level {level} (zoom {z0}-{z1}, {tol:g} m): {len(entries)} files, {len(pieces_all)} pieces, "
            f"{nverts(pieces_all)} vertices, {sum(sizes) / 1024:.0f} KB gzipped (largest file {max(sizes) / 1024:.0f} KB)"
            f"  ({time.time() - t0:.1f} s)")
    head, tiles = level0
    nraw, gz = write_gz(OUT_DIR / "land-0.json.gz", {**head, "tiles": tiles, "index": index})
    log(f"level 0 (zoom {head['zoom'][0]}-{head['zoom'][1]}, {head['tol_m']:g} m): {len(tiles)} pieces, "
        f"{nverts(tiles)} vertices, {nraw / 1024:.0f} KB JSON, {len(gz) / 1024:.0f} KB gzipped")
    log(f"levels 1 to 3: {detail_bytes / 1024:.0f} KB gzipped in all")
    if len(gz) > BUDGET_L0 or detail_bytes > BUDGET_DETAIL:
        log(f"OVER BUDGET: level 0 {len(gz) / 1024:.0f} KB (150), levels 1 to 3 {detail_bytes / 1024:.0f} KB (1536)")
        return 1
    if args.geojson:
        write_geojson(Path(args.geojson), args.level)
    return 0


def load_level(level):
    """(doc header, tiles) of a committed level, every tile file of levels 1 and 2 read."""
    doc = json.loads(gzip.decompress((OUT_DIR / "land-0.json.gz").read_bytes()))
    if level == 0:
        return doc, doc["tiles"]
    tiles = []
    head = None
    for tid in doc["index"][str(level)]["tiles"]:
        t = json.loads(gzip.decompress((OUT_DIR / f"land-{level}-{tid}.json.gz").read_bytes()))
        head = head or t
        tiles.extend(t["tiles"])
    return head, tiles


def write_geojson(path, level):
    head, tiles = load_level(level)
    feats = [{"type": "Feature", "properties": {"tile": t["id"], "level": level},
              "geometry": {"type": "MultiLineString",
                           "coordinates": [[list(p) for p in decode_ring(r, head["q"])]
                                           for r in t["rings"] + t.get("islets", [])]}}
             for t in tiles]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
    log(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


def check():
    doc = json.loads(gzip.decompress((OUT_DIR / "land-0.json.gz").read_bytes()))
    gz0 = (OUT_DIR / "land-0.json.gz").stat().st_size
    log(f"land-0.json.gz: {gz0 / 1024:.0f} KB gzipped, {len(doc['tiles'])} pieces, {nverts(doc['tiles'])} vertices, "
        f"OSM data {doc.get('osm_date')}, built {doc['built']}")
    total = 0
    for level, ent in sorted(doc["index"].items()):
        sizes = [v[1] for v in ent["tiles"].values()]
        total += sum(sizes)
        _, tiles = load_level(int(level))
        log(f"level {level} (zoom {ent['zoom'][0]}-{ent['zoom'][1]}): {len(sizes)} files, {len(tiles)} pieces, "
            f"{nverts(tiles)} vertices, {sum(sizes) / 1024:.0f} KB gzipped, largest {max(sizes) / 1024:.0f} KB")
    log(f"levels 1 to 3: {total / 1024:.0f} KB gzipped")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="print the committed assets' sizes")
    ap.add_argument("--force-download", action="store_true", help="download the OSM land polygons again")
    ap.add_argument("--download-only", action="store_true")
    ap.add_argument("--geojson", help="after building (or with --check), write one level as GeoJSON lines here")
    ap.add_argument("--level", type=int, default=2, help="the level --geojson writes (0 to 3)")
    args = ap.parse_args()
    if args.check:
        rc = check()
        if args.geojson:
            write_geojson(Path(args.geojson), args.level)
        return rc
    if args.download_only:
        download(args.force_download)
        return 0
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
