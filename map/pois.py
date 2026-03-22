"""Points of interest (fishing spots) and AOI outline for the map."""

import numpy as np
import dash_leaflet as dl

POIS = [
    ("Haabs Ledge",         40.868250, -71.838200),
    ("Butterfish Hole",     40.836467, -71.674900),
    ("July2025",            40.895550, -71.830817),
    ("CIA",                 40.933433, -71.716667),
    ("Gully",               41.020483, -71.416950),
    ("Wind Farm SW Corner", 40.973983, -71.273300),
    ("Tuna Ridge",          40.916667, -71.279167),
]


def _lookup_temp(lat, lon, arrF, lats, lons):
    """Find the temperature at the nearest grid point."""
    lat_idx = np.argmin(np.abs(lats - lat))
    lon_idx = np.argmin(np.abs(lons - lon))
    val = arrF[lat_idx, lon_idx]
    return float(val) if np.isfinite(val) else None


def build_poi_markers(arrF=None, lats=None, lons=None):
    """Return CircleMarker components for POIs, with SST if data is available."""
    markers = []
    for name, lat, lon in POIS:
        temp = None
        if arrF is not None and lats is not None and lons is not None:
            temp = _lookup_temp(lat, lon, arrF, lats, lons)

        # Plain string label — reliable across all browsers / dash-leaflet versions
        if temp is not None:
            label = f"{name}\n{temp:.1f}°F"
        else:
            label = name

        markers.append(
            dl.CircleMarker(
                center=[lat, lon],
                radius=6,
                pathOptions={
                    "color": "#16a34a",
                    "weight": 2,
                    "fill": True,
                    "fillColor": "#16a34a",
                    "fillOpacity": 0.9,
                },
                children=[
                    dl.Tooltip(
                        label,
                        direction="top",
                        offset=[0, -10],
                        pane="tooltipPane",
                    )
                ],
            )
        )
    return markers


def build_aoi_geojson(config: dict) -> dict:
    """Return GeoJSON for the AOI polygon outline."""
    coords = config["aoi_polygon_lonlat"]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "AOI"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [coords],
                },
            }
        ],
    }
