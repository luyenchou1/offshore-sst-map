"""The /sst coastline: static assets built offline by tools/build_land.py from OpenStreetMap land polygons.

assets/sst/land/land-0.json.gz (the whole box, zoom 5 to 9, about 115 KB) and land-<1|2|3>-<tile>.json.gz (zoom 10,
11 and 12, one file per 1-degree tile, about 1.3 MB in all) are committed with the code ("GOLAND1"). app.py serves
them at /api/sst/land/<name>.json with Content-Encoding: gzip, read once each and kept in memory; the page asks
for level 0 with ?v=<its hash> (layout/mapview.py) and for each tile with the hash level 0 lists, so browsers keep
them for a week. The browser draws them (assets/sstland.js, assets/sstengine.js). No outside call at request time.
Data (c) OpenStreetMap contributors, ODbL 1.0 (assets/sst/land/README.txt).
"""

import hashlib
import re
import threading
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "assets" / "sst" / "land"
ROUTE = "/api/sst/land/<name>.json"
PREFIX = "/api/sst/land/"
NAME_RE = re.compile(r"^(0|[1-3]-[we]\d{2}n\d{2})$")

_cache = {}
_lock = threading.Lock()          # guards the dict only (file reads are local and tiny; no network)


def land_gz(name):
    """(gzip bytes, etag) of one asset ("0", "1-w73n40", ...), or (None, None) if the name is bad or missing."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        return None, None
    hit = _cache.get(name)
    if hit:
        return hit
    try:
        body = (DIR / f"land-{name}.json.gz").read_bytes()
    except OSError:
        return None, None
    out = (body, hashlib.sha1(body).hexdigest()[:10])
    with _lock:
        _cache[name] = out
    return out


def land_url():
    """Level 0's URL with its version, for the page (#sst-look-data data-land)."""
    _, etag = land_gz("0")
    return f"{PREFIX}0.json?v={etag}" if etag else f"{PREFIX}0.json"
