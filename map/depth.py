"""The /sst depth contours: a static asset built offline by tools/build_contours.py from NOAA ETOPO 2022.

The file (assets/sst/contours.json.gz, GOCONT1: 30, 40 and 100 fathoms; 500, 1000 and 2000 m, about 60 KB) is
committed with the code and read once; app.py serves it at /api/sst/contours.json with Content-Encoding: gzip
(like the Currents frames) and the map draws it in the browser (assets/sstlook.js). No outside call at request
time.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "assets" / "sst" / "contours.json.gz"
ROUTE = "/api/sst/contours.json"


@lru_cache(maxsize=1)
def contours_gz():
    """(gzip bytes, etag) of the asset, or (None, None) if it's missing."""
    try:
        body = PATH.read_bytes()
    except OSError:
        return None, None
    return body, hashlib.sha1(body).hexdigest()[:12]


def contours_url():
    """The asset's URL with its version, so browsers can keep it for a week and still see a rebuild."""
    _, etag = contours_gz()
    return f"{ROUTE}?v={etag}" if etag else ROUTE
