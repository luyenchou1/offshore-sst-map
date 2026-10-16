"""Distance and bearing calculations for the measure tool."""

import math
from datetime import date

from map import declination as _decl


def haversine_nm(lat1, lon1, lat2, lon2):
    """Return great-circle distance in nautical miles."""
    R_NM = 3440.065  # Earth radius in nautical miles
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R_NM * math.asin(math.sqrt(a))


def initial_bearing(lat1, lon1, lat2, lon2):
    """Return initial bearing in degrees (0-360) from point A to point B."""
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (lat1, lon1, lat2, lon2))
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    bearing = math.degrees(math.atan2(x, y))
    return (bearing + 360) % 360


def compass_direction(bearing):
    """Convert bearing degrees to compass label (N, NE, E, etc.)."""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = round(bearing / 45) % 8
    return dirs[idx]


def magnetic_variation(lat, lon, when=None):
    """Declination in degrees (east positive; this area is about 12 to 15 west,
    so negative) from the WMM-2025 grid in map/declination.py (bilinear, clamped
    to the grid, moved along each point's yearly change)."""
    when = when or date.today()
    year = when.year + (when.timetuple().tm_yday - 0.5) / 365.25
    lats, lons, step = _decl.LATS, _decl.LONS, _decl.STEP
    la = min(max(lat, lats[0]), lats[-1])
    lo = min(max(lon, lons[0]), lons[-1])
    i = min(int((la - lats[0]) / step), len(lats) - 2)
    j = min(int((lo - lons[0]) / step), len(lons) - 2)
    ty = (la - lats[i]) / step
    tx = (lo - lons[j]) / step

    def at(g):
        return ((1 - ty) * ((1 - tx) * g[i][j] + tx * g[i][j + 1])
                + ty * ((1 - tx) * g[i + 1][j] + tx * g[i + 1][j + 1]))

    return at(_decl.DECL) + at(_decl.RATE) * (year - _decl.EPOCH)


def format_measurement(lat1, lon1, lat2, lon2, when=None):
    """Distance, true course and magnetic course from A to B.

    The magnetic course uses the variation at A (where you'd set the course):
    magnetic = true - declination, so 13 degrees west adds 13 to a true course.
    """
    dist_nm = haversine_nm(lat1, lon1, lat2, lon2)
    dist_mi = dist_nm * 1.15078
    bearing = initial_bearing(lat1, lon1, lat2, lon2)
    compass = compass_direction(bearing)
    var = magnetic_variation(lat1, lon1, when)
    mag = (bearing - var) % 360
    var_text = f"{abs(var):.1f}° {'W' if var < 0 else 'E'}"
    true_txt = f"{round(bearing) % 360:03d}°"
    mag_txt = f"{round(mag) % 360:03d}°"
    return {
        "nm": dist_nm,
        "mi": dist_mi,
        "bearing": bearing,
        "magnetic": mag,
        "true_text": true_txt,
        "magnetic_text": mag_txt,
        "variation": var,
        "variation_text": var_text,
        "compass": compass,
        "label": f"{dist_nm:.1f} nm · {true_txt} true · {mag_txt} magnetic",
    }
