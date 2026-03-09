"""Points of interest (fishing spots) and AOI outline for the map."""

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


def build_poi_markers():
    """Return a list of dash-leaflet CircleMarker components for POIs."""
    markers = []
    for name, lat, lon in POIS:
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
                children=[dl.Tooltip(name)],
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
