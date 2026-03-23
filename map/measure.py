"""Distance and bearing calculations for the ruler tool."""

import math


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


def format_measurement(lat1, lon1, lat2, lon2):
    """Return formatted distance and heading string."""
    dist_nm = haversine_nm(lat1, lon1, lat2, lon2)
    dist_mi = dist_nm * 1.15078
    bearing = initial_bearing(lat1, lon1, lat2, lon2)
    compass = compass_direction(bearing)
    return {
        "nm": dist_nm,
        "mi": dist_mi,
        "bearing": bearing,
        "compass": compass,
        "label": f"{dist_nm:.1f} nm ({dist_mi:.1f} mi) \u2022 {bearing:.0f}\u00b0 {compass}",
    }
