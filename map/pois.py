"""Points of interest (fishing spots) and AOI outline for the map."""

import math

import numpy as np
import dash_leaflet as dl
from dash import html

# ---- All available fishing spots (name, lat, lon) ----
ALL_POIS = [
    # Original spots
    ("Haabs Ledge",         40.868250, -71.838200),
    ("Butterfish Hole",     40.836467, -71.674900),
    ("Rachel's Whales",     40.895550, -71.830817),
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
    # Canyons (shelf edge — major tuna grounds)
    ("Hudson Canyon",       39.540000, -72.050000),
    ("Block Canyon",        39.730000, -71.750000),
    ("Toms Canyon",         39.500000, -72.600000),
    ("Lindenkohl Canyon",   39.450000, -72.350000),
    ("Spencer Canyon",      39.100000, -73.150000),
    ("Veatch Canyon",       40.050000, -69.550000),
    ("Hydrographer Canyon", 40.100000, -69.300000),
    # Banks & ledges (tuna staging/feeding areas)
    ("17 Fathom Bank",      39.650000, -73.100000),
    ("Cholera Bank",        40.050000, -73.200000),
    ("The Fingers",         40.700000, -70.600000),
    ("Stellwagen Bank",     42.350000, -70.350000),
    ("Jeffreys Ledge",      42.850000, -70.100000),
    ("Platts Bank",         43.200000, -69.700000),
    ("Cashes Ledge",        42.900000, -69.000000),
]

# ---- The Dump: defined as a box (source: saltycape.com/the-dump/) ----
DUMP_BOX = {
    "name": "The Dump",
    "nw": (40.833, -70.996),
    "ne": (40.833, -70.750),
    "sw": (40.667, -70.996),
    "se": (40.667, -70.750),
}

# Marker style: subtle hollow rings in muted slate
_MARKER_STYLE = {
    "color": "#0a1628",
    "weight": 2,
    "fill": True,
    "fillColor": "#ffffff",
    "fillOpacity": 0.9,
}

_DUMP_STYLE = {
    "color": "#0a1628",
    "weight": 2,
    "fill": True,
    "fillColor": "#ffffff",
    "fillOpacity": 0.15,
    "dashArray": "6 4",
}


def get_poi_options():
    """Return list of {label, value} for the multi-select dropdown."""
    options = [{"label": name, "value": name} for name, _, _ in ALL_POIS]
    options.append({"label": DUMP_BOX["name"], "value": DUMP_BOX["name"]})
    return options


def get_all_poi_names():
    """Return list of all POI names (for default selection)."""
    names = [name for name, _, _ in ALL_POIS]
    names.append(DUMP_BOX["name"])
    return names


def _lookup_temp(lat, lon, arrF, lats, lons):
    """Find the temperature at the nearest grid point."""
    lat_idx = np.argmin(np.abs(lats - lat))
    lon_idx = np.argmin(np.abs(lons - lon))
    val = arrF[lat_idx, lon_idx]
    return float(val) if np.isfinite(val) else None


def find_nearest_poi(lat, lng, selected=None, threshold_deg=0.08):
    """Find the nearest POI to a click within threshold_deg (~5 nm).

    Returns (name, poi_lat, poi_lon) or None if no POI is close enough.
    Also checks if click is inside The Dump rectangle.
    """
    selected_set = set(selected) if selected is not None else None

    # Check The Dump rectangle first
    d = DUMP_BOX
    if selected_set is None or d["name"] in selected_set:
        if (d["sw"][0] <= lat <= d["nw"][0] and
                d["sw"][1] <= lng <= d["ne"][1]):
            center_lat = (d["nw"][0] + d["sw"][0]) / 2
            center_lon = (d["nw"][1] + d["ne"][1]) / 2
            return (d["name"], center_lat, center_lon)

    # Check point POIs
    best = None
    best_dist = threshold_deg
    for name, plat, plon in ALL_POIS:
        if selected_set is not None and name not in selected_set:
            continue
        dist = math.sqrt((lat - plat) ** 2 + (lng - plon) ** 2)
        if dist < best_dist:
            best_dist = dist
            best = (name, plat, plon)

    return best


def build_poi_tooltip(name, lat, lon, temp=None):
    """Build a tooltip component for a POI (shown in click-marker layer)."""
    children = [
        html.Div(name, style={
            "fontSize": "0.85rem", "fontWeight": "700",
            "color": "#1e293b", "lineHeight": "1.2",
        }),
    ]
    if temp is not None:
        children.append(
            html.Div(f"{temp:.1f}°F", style={
                "fontSize": "1rem", "fontWeight": "700",
                "color": "#334155", "marginTop": "2px",
            }),
        )
    children.append(
        html.Div(f"{lat:.3f}°N, {abs(lon):.3f}°W", style={
            "fontSize": "0.65rem", "color": "#94a3b8",
            "marginTop": "2px",
        }),
    )
    return html.Div(
        children,
        style={
            "textAlign": "center",
            "borderLeft": "3px solid #64748b",
            "paddingLeft": "6px",
        },
    )


def build_poi_markers(arrF=None, lats=None, lons=None, selected=None):
    """Return visual-only marker components for selected POIs + The Dump.

    Markers have no Popup/Tooltip children — click handling is done
    via the map click callback using find_nearest_poi().
    """
    if selected is not None and len(selected) == 0:
        return []

    markers = []
    selected_set = set(selected) if selected is not None else None

    # Point markers (visual only — no popup/tooltip)
    for name, lat, lon in ALL_POIS:
        if selected_set is not None and name not in selected_set:
            continue
        markers.append(
            dl.CircleMarker(
                center=[lat, lon],
                radius=6,
                pathOptions=_MARKER_STYLE,
                bubblingMouseEvents=True,  # let clicks pass through to map
            )
        )

    # The Dump rectangle (visual only)
    if selected_set is None or DUMP_BOX["name"] in selected_set:
        d = DUMP_BOX
        markers.append(
            dl.Rectangle(
                bounds=[[d["sw"][0], d["sw"][1]], [d["ne"][0], d["ne"][1]]],
                pathOptions=_DUMP_STYLE,
                bubblingMouseEvents=True,
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
