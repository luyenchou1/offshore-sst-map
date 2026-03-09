"""Build GeoJSON FeatureCollection for hover tooltips on the SST grid."""

import numpy as np


def build_tooltip_geojson(
    arrF: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    mode: str = "Normal",
) -> dict:
    """Return a GeoJSON FeatureCollection of points with temp_f properties.

    The density *mode* controls how many points are included:
      Sparse  → ~40 points per axis
      Normal  → ~60 points per axis
      Dense   → ~90 points per axis
    """
    base = {"Sparse": 40, "Normal": 60, "Dense": 90}.get(mode, 60)
    stride_lat = max(1, len(lats) // base)
    stride_lon = max(1, len(lons) // base)

    features = []
    for i in range(0, len(lats), stride_lat):
        for j in range(0, len(lons), stride_lon):
            v = arrF[i, j]
            if not np.isfinite(v):
                continue
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [float(lons[j]), float(lats[i])],
                    },
                    "properties": {"temp_f": int(v)},
                }
            )

    return {"type": "FeatureCollection", "features": features}
