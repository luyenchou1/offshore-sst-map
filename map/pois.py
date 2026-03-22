"""Points of interest (fishing spots) and AOI outline for the map."""

import numpy as np
import dash_leaflet as dl
from dash import html

# ---- All available fishing spots (name, lat, lon) ----
ALL_POIS = [
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

# Marker style: subtle hollow rings in muted slate
_MARKER_STYLE = {
    "color": "#475569",
    "weight": 1.5,
    "fill": True,
    "fillColor": "#475569",
    "fillOpacity": 0.15,
}

_DUMP_STYLE = {
    "color": "#475569",
    "weight": 1.5,
    "fill": True,
    "fillColor": "#475569",
    "fillOpacity": 0.06,
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


def build_poi_markers(arrF=None, lats=None, lons=None, selected=None):
    """Return marker components for selected POIs + The Dump rectangle.

    Args:
        selected: list of POI names to show, or None for all.
    """
    if selected is not None and len(selected) == 0:
        return []

    markers = []
    selected_set = set(selected) if selected is not None else None

    # Point markers
    for name, lat, lon in ALL_POIS:
        if selected_set is not None and name not in selected_set:
            continue

        temp = None
        if arrF is not None and lats is not None and lons is not None:
            temp = _lookup_temp(lat, lon, arrF, lats, lons)

        tooltip_children = [
            html.Div(name, style={
                "fontSize": "0.85rem", "fontWeight": "700",
                "color": "#1e293b", "lineHeight": "1.2",
            }),
        ]
        if temp is not None:
            tooltip_children.append(
                html.Div(f"{temp:.1f}°F", style={
                    "fontSize": "1rem", "fontWeight": "700",
                    "color": "#334155", "marginTop": "2px",
                }),
            )
        tooltip_children.append(
            html.Div(f"{lat:.3f}°N, {abs(lon):.3f}°W", style={
                "fontSize": "0.65rem", "color": "#94a3b8",
                "marginTop": "2px",
            }),
        )

        markers.append(
            dl.CircleMarker(
                center=[lat, lon],
                radius=6,
                pathOptions=_MARKER_STYLE,
                children=[
                    dl.Tooltip(
                        html.Div(
                            tooltip_children,
                            style={
                                "textAlign": "center",
                                "borderLeft": "3px solid #64748b",
                                "paddingLeft": "6px",
                            },
                        ),
                        direction="top",
                        offset=[0, -10],
                        pane="tooltipPane",
                    )
                ],
            )
        )

    # The Dump — rectangle overlay with center temp
    if selected_set is None or DUMP_BOX["name"] in selected_set:
        d = DUMP_BOX
        center_lat = (d["nw"][0] + d["sw"][0]) / 2
        center_lon = (d["nw"][1] + d["ne"][1]) / 2
        temp = None
        if arrF is not None and lats is not None and lons is not None:
            temp = _lookup_temp(center_lat, center_lon, arrF, lats, lons)

        dump_children = [
            html.Div(d["name"], style={
                "fontSize": "0.85rem", "fontWeight": "700",
                "color": "#1e293b", "lineHeight": "1.2",
            }),
        ]
        if temp is not None:
            dump_children.append(
                html.Div(f"{temp:.1f}°F (center)", style={
                    "fontSize": "1rem", "fontWeight": "700",
                    "color": "#334155", "marginTop": "2px",
                }),
            )
        dump_children.append(
            html.Div(
                f"{center_lat:.3f}°N, {abs(center_lon):.3f}°W",
                style={
                    "fontSize": "0.65rem", "color": "#94a3b8",
                    "marginTop": "2px",
                },
            ),
        )

        markers.append(
            dl.Rectangle(
                bounds=[[d["sw"][0], d["sw"][1]], [d["ne"][0], d["ne"][1]]],
                pathOptions=_DUMP_STYLE,
                children=[
                    dl.Tooltip(
                        html.Div(
                            dump_children,
                            style={
                                "textAlign": "center",
                                "borderLeft": "3px solid #64748b",
                                "paddingLeft": "6px",
                            },
                        ),
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
