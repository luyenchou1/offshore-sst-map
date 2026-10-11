"""Tidal-current animation data for the /currents page.

Shared by the Flask endpoints in app.py and the offline builder
tools/build_currents.py. Deliberately stdlib-only (plus numpy, imported
lazily) so the builder can import it without pulling in the web app.

Files live in CURRENTS_DIR (on Render: /var/data/cache/currents, i.e. a
subfolder of the persistent disk that SST_CACHE_DIR points at):

  manifest.json                 index: weeks, stations, and the "atlas" entry
  <week>_surface.bin.gz         hourly surface-current frames
  <week>_davg.bin.gz            hourly depth-averaged-current frames
  <week>_meta.json.gz           NOAA tide/current predictions for the week

The tide-stage atlas (tools/build_tide_atlas.py) uses the same names and
format with id "atlas-<year>": its frame files hold basis fields per tide
phase bin instead of hourly frames (frame = basis * bins + bin; t0 = 0,
dt = mean tide cycle / bins), and its meta has an "atlas" key instead of
"week" (phase/range model, presets, skill maps, station curves, bundled
Montauk high/low predictions).

Frame file format "GOCUR1" (little-endian), gzip-compressed on disk:

  off  type  field
  0    6s    magic b"GOCUR1"
  6    H     version (1)
  8    H     nx        grid columns, west -> east
  10   H     ny        grid rows, north -> south (row 0 = lat1)
  12   H     nframes   hourly frames
  14   H     reserved
  16   d     t0        epoch seconds (UTC) of frame 0
  24   f     dt        seconds between frames
  28   f     lon0      west edge
  32   f     lat0      south edge
  36   f     lon1      east edge
  40   f     lat1      north edge
  44   f     scale     m/s per int8 unit
  48   B     depth     0 = surface, 1 = depth-averaged
  49   15x   padding -> 64-byte header
  64   uint8[ny*nx]               water mask (1 = water)
  ..   int8[nframes][2][ny][nx]   u (east) then v (north), value * scale = m/s

Values sit on grid *points*: lon_i = lon0 + i*(lon1-lon0)/(nx-1),
lat_j = lat1 - j*(lat1-lat0)/(ny-1).
"""

import gzip
import io
import json
import os
import re
import struct
from pathlib import Path

CURRENTS_DIR = Path(
    os.environ.get("CURRENTS_DATA_DIR")
    or (Path(os.environ.get("SST_CACHE_DIR", "./cache")) / "currents")
)

# Eastern Long Island: Little Peconic -> east of Block Island,
# south of Montauk -> Watch Hill / Fishers / eastern Long Island Sound.
REGION_BBOX = (-72.45, 40.93, -71.40, 41.38)  # lon0, lat0, lon1, lat1

MAGIC = b"GOCUR1"
VERSION = 1
HEADER_FMT = "<6sHHHHHdffffffB15x"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 64
DEPTHS = {"surface": 0, "davg": 1}

WEEK_ID = r"[a-z0-9][a-z0-9-]{2,39}"
WEEK_ID_RE = re.compile(rf"^{WEEK_ID}$")
FILE_RE = re.compile(
    rf"^(manifest\.json|{WEEK_ID}_(surface|davg)\.bin\.gz|{WEEK_ID}_meta\.json\.gz)$"
)

MAX_UPLOAD_BYTES = 12 * 1024 * 1024     # compressed; served files must stay
                                        # well under Render's ~7-10 MB proxy limit
MAX_UNZIPPED_BYTES = 64 * 1024 * 1024   # zip-bomb guard when validating uploads


def pack_frames(u_q, v_q, mask, *, t0, dt, bbox, scale, depth):
    """Pack quantized frames into an (uncompressed) GOCUR1 byte string.

    u_q, v_q: int8 arrays shaped (nframes, ny, nx); mask: uint8 (ny, nx).
    """
    import numpy as np

    nf, ny, nx = u_q.shape
    if v_q.shape != u_q.shape or mask.shape != (ny, nx):
        raise ValueError("u/v/mask shapes disagree")
    lon0, lat0, lon1, lat1 = bbox
    header = struct.pack(
        HEADER_FMT, MAGIC, VERSION, nx, ny, nf, 0,
        float(t0), float(dt), lon0, lat0, lon1, lat1, float(scale),
        DEPTHS[depth] if isinstance(depth, str) else int(depth),
    )
    frames = np.stack([u_q, v_q], axis=1).astype(np.int8)  # (nf, 2, ny, nx)
    return header + mask.astype(np.uint8).tobytes() + frames.tobytes()


def parse_header(raw):
    """Return the header fields of an uncompressed GOCUR1 byte string."""
    if len(raw) < HEADER_SIZE:
        raise ValueError("file shorter than header")
    (magic, version, nx, ny, nf, _r, t0, dt,
     lon0, lat0, lon1, lat1, scale, depth) = struct.unpack_from(HEADER_FMT, raw)
    if magic != MAGIC:
        raise ValueError(f"bad magic {magic!r}")
    if version != VERSION:
        raise ValueError(f"unsupported version {version}")
    return {
        "nx": nx, "ny": ny, "nframes": nf, "t0": t0, "dt": dt,
        "bbox": (lon0, lat0, lon1, lat1), "scale": scale, "depth": depth,
    }


def expected_size(h):
    cells = h["nx"] * h["ny"]
    return HEADER_SIZE + cells + h["nframes"] * 2 * cells


def unpack_frames(raw):
    """Decode an uncompressed GOCUR1 byte string (used by tests/checks)."""
    import numpy as np

    h = parse_header(raw)
    if len(raw) != expected_size(h):
        raise ValueError("length does not match header")
    ny, nx, nf = h["ny"], h["nx"], h["nframes"]
    off = HEADER_SIZE
    mask = np.frombuffer(raw, np.uint8, ny * nx, off).reshape(ny, nx)
    frames = np.frombuffer(raw, np.int8, nf * 2 * ny * nx, off + ny * nx)
    frames = frames.reshape(nf, 2, ny, nx)
    return h, mask, frames[:, 0], frames[:, 1]


def _gunzip_bounded(data):
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
        out = f.read(MAX_UNZIPPED_BYTES + 1)
    if len(out) > MAX_UNZIPPED_BYTES:
        raise ValueError("decompressed size too large")
    return out


def validate_upload(fname, data):
    """Return an error string if (fname, data) is not a valid currents file."""
    if not FILE_RE.fullmatch(fname or ""):
        return f"invalid filename: {fname!r}"
    if not data:
        return "empty body"
    try:
        if fname == "manifest.json":
            m = json.loads(data.decode("utf-8"))
            if not isinstance(m.get("weeks"), list):
                return "manifest missing 'weeks' list"
            return None
        raw = _gunzip_bounded(data)
        if fname.endswith("_meta.json.gz"):
            meta = json.loads(raw.decode("utf-8"))
            for key in ("tide", "stations"):
                if key not in meta:
                    return f"meta missing '{key}'"
            if "week" not in meta and "atlas" not in meta:
                return "meta missing 'week' or 'atlas'"
            return None
        h = parse_header(raw)
        if not (2 <= h["nx"] <= 2000 and 2 <= h["ny"] <= 2000 and 1 <= h["nframes"] <= 2000):
            return "implausible grid dimensions"
        if len(raw) != expected_size(h):
            return "length does not match header"
        want = DEPTHS["davg"] if fname.endswith("_davg.bin.gz") else DEPTHS["surface"]
        if h["depth"] != want:
            return "depth flag does not match filename"
    except Exception as e:  # noqa: BLE001 - any parse failure is a rejection
        return f"not a valid currents file: {e}"
    return None


def read_manifest():
    """Load manifest.json from CURRENTS_DIR, or None if absent/unreadable."""
    p = CURRENTS_DIR / "manifest.json"
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
