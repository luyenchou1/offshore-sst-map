"""Points of interest (fishing spots) and AOI outline for the map."""

import numpy as np
import dash_leaflet as dl

# ---- Point POIs (name, lat, lon) ----
POIS = [
    # Original spots
    ("Haabs Ledge",         40.868250, -71.838200),
    ("Butterfish Hole",     40.836467, -71.674900),
    ("July2025",            40.895550, -71.830817),
    ("CIA",                 40.933433, -71.716667),
    ("Gully",               41.020483, -71.416950),
    ("Wind Farm SW Corner", 40.973983, -71.273300),
    ("Tuna Ridge",          40.916667, -71.279167),
    # New spots (source: marinebasin.com/fishing_spots)
    ("Bacardi Wreck",       39.882800, -72.645300),
    ("Coimbra Wreck",       40.401300, -72.338700),
    ("Cartwright",          41.000000, -71.808300),
    ("Coxes Ledge",         41.050000, -71.158300),
    ("West Atlantis",       40.075000, -70.450000),
    ("East Atlantis",       39.958300, -69.916700),
    ("The Dip",             39.908300, -71.733300),
    ("Fish Tails",          40.061900, -71.355400),
    ("Jennie's Horn",       40.812500, -71.544200),
    ("Mud Hole",            40.937300, -71.417300),
    ("Ranger Wreck",        40.588300, -71.790000),
]

# ---- The Dump: defined as a box (source: saltycape.com/the-dump/) ----
DUMP_BOX = {
    "name": "The Dump",
    "nw": (40.833, -70.996),
    "ne": (40.833, -70.750),
    "sw": (40.667, -70.996),
    "se": (40.667, -70.750),
}


def _lookup_temp(lat, lon, arrF, lats, lons):
    """Find the temperature at the nearest grid point."""
    lat_idx = np.argmin(np.abs(lats - lat))
    lon_idx = np.argmin(np.abs(lons - lon))
    val = arrF[lat_idx, lon_idx]
    return float(val) if np.isfinite(val) else None


def build_poi_markers(arrF=None, lats=None, lons=None, show=True):
    """Return CircleMarker components for POIs + The Dump rectangle.

    If show=False, returns an empty list (POIs hidden by toggle).
    """
    if not show:
        return []

    markers = []

    # Point markers
    for name, lat, lon in POIS:
        temp = None
        if arrF is not None and lats is not None and lons is not None:
            temp = _lookup_temp(lat, lon, arrF, lats, lons)

        if temp is not None:
            label = f"{name}\n{temp:.1f}°F"
        else:
            label = name

        markers.append(
            dl.CircleMarker(
                center=[lat, lon],
                radius=5,
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

    # The Dump — rectangle overlay with center temp
    d = DUMP_BOX
    center_lat = (d["nw"][0] + d["sw"][0]) / 2
    center_lon = (d["nw"][1] + d["ne"][1]) / 2
    temp = None
    if arrF is not None and lats is not None and lons is not None:
        temp = _lookup_temp(center_lat, center_lon, arrF, lats, lons)

    if temp is not None:
        dump_label = f"{d['name']}\n{temp:.1f}°F"
    else:
        dump_label = d["name"]

    markers.append(
        dl.Rectangle(
            bounds=[[d["sw"][0], d["sw"][1]], [d["ne"][0], d["ne"][1]]],
            pathOptions={
                "color": "#16a34a",
                "weight": 2,
                "fill": True,
                "fillColor": "#16a34a",
                "fillOpacity": 0.08,
                "dashArray": "6 4",
            },
            children=[
                dl.Tooltip(
                    dump_label,
                    direction="top",
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
