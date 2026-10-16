"""SST array to base64 PNG for dash-leaflet ImageOverlay.

Leaflet stretches an ImageOverlay linearly in Web Mercator between its bounds, but the SST grid's rows are evenly
spaced in latitude. Drawn as is, over 38.8 to 43.8 N the middle of the image sat up to 5.3 km (2.9 nm) north of
true, worst near 41.3 N (designer's finding, Oct 16 2026). So each frame's rows are resampled first
(`mercator_rows`): output row r is centred where Leaflet draws it, and holds the source row whose cell contains that
latitude (nearest, so values, land and the AOI mask are exactly the raw grid's). Output rows are at most
MAX_ROW_DEG tall, so a cell's edge lands within 0.28 km of true (Leaflet then rounds the bounds to whole screen
pixels). Only the picture changes: the raw arrays (tap readouts) and the bounds stay as they are.
"""

import base64
import io
import math

import numpy as np
from PIL import Image
import matplotlib.colors as mcolors

from map.colorscale import SST_COLORS

# Frames rendered by this module say so in the cache entry ("frame_format"). Entries without it (or older) hold
# frames whose rows are evenly spaced in latitude: the app re-renders them from the entry's raw arrays (no NOAA).
#   1 (no key): rows evenly spaced in latitude (before Oct 16, 2026)
#   2: rows evenly spaced in Web Mercator (mercator_rows)
FRAME_FORMAT = 2
MAX_ROW_DEG = 0.005      # output row height, degrees of latitude: 0.56 km, half a MUR cell
MAX_ROWS = 4096


def _build_mpl_cmap():
    return mcolors.LinearSegmentedColormap.from_list("sst", SST_COLORS, N=256)


def array_to_rgba(arrF: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """Vectorized color mapping — replaces the old per-pixel Python loop."""
    cmap = _build_mpl_cmap()
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

    normalized = norm(arrF)
    rgba = (cmap(normalized) * 255).astype(np.uint8)

    # Set NaN pixels to fully transparent
    nan_mask = np.isnan(arrF)
    rgba[nan_mask] = [0, 0, 0, 0]
    return rgba


def merc_y(lat_deg):
    """Web Mercator y (radians) of a latitude in degrees."""
    return np.log(np.tan(np.pi / 4 + np.radians(lat_deg) / 2))


def merc_lat(y):
    """Latitude (degrees) of a Web Mercator y."""
    return np.degrees(2 * np.arctan(np.exp(y)) - np.pi / 2)


def mercator_row_sources(lats, south: float, north: float) -> np.ndarray:
    """For each output row (row 0 north), the source row it shows.

    lats are the source rows' centre latitudes, north first (orient_to_leaflet); south / north are the overlay's
    bounds (the grid's outer edges). Output rows are evenly spaced in Web Mercator between south and north, as
    Leaflet draws them, and at most MAX_ROW_DEG (and half a source row) tall, so no source row is skipped. Each takes
    the source row with the nearest centre to its own centre latitude.
    """
    lats = np.asarray(lats, dtype=np.float64)
    n = len(lats)
    if n < 2 or not north > south:
        return np.arange(n)
    if lats[0] < lats[-1]:
        raise ValueError("mercator_row_sources: rows must run north to south (orient_to_leaflet)")
    y_s, y_n = float(merc_y(south)), float(merc_y(north))
    step = min(MAX_ROW_DEG, float(np.median(np.abs(np.diff(lats)))) / 2)
    # A row is tallest in latitude where cos(lat) is largest (the edge nearest the equator)
    widest = 1.0 if south <= 0 <= north else math.cos(math.radians(min(abs(south), abs(north))))
    rows = int(min(MAX_ROWS, max(n, math.ceil((y_n - y_s) * widest / math.radians(step)))))
    centre = merc_lat(y_n - (np.arange(rows) + 0.5) / rows * (y_n - y_s))
    asc = lats[::-1]                                   # np.interp wants ascending x
    frac = np.interp(centre, asc, np.arange(n - 1, -1, -1, dtype=np.float64))
    return np.clip(np.rint(frac), 0, n - 1).astype(np.intp)


def mercator_rows(arrF: np.ndarray, lats, south: float, north: float) -> np.ndarray:
    """arrF's rows resampled to Web Mercator spacing between south and north (see mercator_row_sources)."""
    return arrF[mercator_row_sources(lats, south, north)]


def sst_to_base64_png(arrF: np.ndarray, vmin: float, vmax: float, *, lats, bounds) -> str:
    """A data-URI PNG of one day for dl.ImageOverlay(bounds=bounds), rows resampled to Web Mercator.

    lats: the rows' centre latitudes (north first); bounds: [[south, west], [north, east]] (geo.grid_bounds), the
    same bounds the overlay is given.
    """
    rgba = array_to_rgba(mercator_rows(arrF, lats, bounds[0][0], bounds[1][0]), vmin, vmax)
    img = Image.fromarray(rgba, "RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True, compress_level=9)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"
