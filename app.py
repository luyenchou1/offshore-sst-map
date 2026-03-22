"""Offshore SST Map — NJ to MA AOI (Dash + Dash Leaflet)

Daily sea-surface temperature visualization for the offshore corridor
from New York Harbor to Massachusetts. Data from NOAA CoastWatch ERDDAP
(MUR GHRSST preferred, OISST fallback). Temperatures in °F.
"""

import json
import logging
from datetime import datetime, timedelta, timezone

import dash
import dash_bootstrap_components as dbc
import dash_leaflet as dl
import numpy as np
from dash import Input, Output, State, dcc, html

from data.convert import upsample_visual
from data.erddap import get_sst
from data.geo import mask_aoi_rasterized, mask_land_rasterized, orient_to_leaflet
from layout.mapview import build_map
from layout.sidebar import build_sidebar
from map.colorscale import build_legend_component, compute_color_bounds
from map.overlay import sst_to_base64_png
from map.pois import build_aoi_geojson, build_poi_markers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open("config.json") as f:
    CFG = json.load(f)

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    title="Offshore SST (NJ→MA)",
)
server = app.server  # for gunicorn

app.layout = dbc.Container(
    [
        dbc.Row(
            dbc.Col(
                [
                    html.H3(
                        "Offshore SST — NJ to MA AOI",
                        className="mt-2 mb-0",
                    ),
                    html.P(
                        "Daily SST from NOAA CoastWatch ERDDAP (MUR preferred). "
                        "Temperatures in °F. Click the map to read temperatures.",
                        className="text-muted mb-2",
                        style={"fontSize": "0.85rem"},
                    ),
                ]
            )
        ),
        dbc.Row([build_sidebar(), build_map()]),
        dcc.Store(id="sst-store"),
        dcc.Loading(
            id="loading-overlay",
            type="default",
            children=html.Div(id="loading-target"),
            fullscreen=True,
            style={"backgroundColor": "rgba(255,255,255,0.7)"},
        ),
        # Auto-fetch SST on page load (fires once after 500ms)
        dcc.Interval(id="auto-fetch", interval=500, max_intervals=1),
    ],
    fluid=True,
    style={"padding": "0 0.5rem"},
)


# ---- Callback 1: Fetch SST data ----
@app.callback(
    Output("sst-store", "data"),
    Output("fetch-status", "children"),
    Output("loading-target", "children"),
    Input("fetch-btn", "n_clicks"),
    Input("auto-fetch", "n_intervals"),
    State("days-back", "value"),
    prevent_initial_call=True,
)
def fetch_sst_data(n_clicks, n_intervals, days_back):
    target_date = datetime.now(timezone.utc).date() - timedelta(days=days_back - 1)
    try:
        sst = get_sst(target_date, CFG)

        # Pre-process: orient and mask once so downstream callbacks are simpler
        arrF = sst["arrF"]
        lats = sst["lats"]
        lons = sst["lons"]
        arrF, lats, lons = orient_to_leaflet(arrF, lats, lons)
        arrF = mask_aoi_rasterized(arrF, lats, lons, CFG)
        arrF = mask_land_rasterized(arrF, lats, lons)

        payload = {
            "server": sst["server"],
            "dataset_id": sst["dataset_id"],
            "dataset_title": sst["dataset_title"],
            "var": sst["var"],
            "units": sst["units"],
            "arrF": arrF.tolist(),
            "lats": lats.tolist(),
            "lons": lons.tolist(),
            "date_used": sst.get("date_used", str(target_date)),
        }
        date_str = sst.get("date_used", str(target_date))
        status = dbc.Alert(
            [
                html.Strong(f"Loaded: {date_str}"),
                html.Br(),
                f"{sst['dataset_id']}",
            ],
            color="success",
            className="py-2 px-3 mb-0",
            style={"fontSize": "0.8rem"},
        )
        return payload, status, ""
    except Exception as e:
        logger.exception("SST fetch failed")
        return (
            dash.no_update,
            dbc.Alert(f"Error: {e}", color="danger", className="py-2 px-3 mb-0"),
            "",
        )


# ---- Callback 2: Render map layers ----
@app.callback(
    Output("sst-overlay", "url"),
    Output("sst-overlay", "bounds"),
    Output("aoi-outline", "data"),
    Output("poi-layer", "children"),
    Output("legend-container", "children"),
    Input("sst-store", "data"),
    Input("lock-scale", "value"),
    Input("upsample-factor", "value"),
)
def render_map_layers(sst_data, lock_scale, up_factor):
    aoi_geojson = build_aoi_geojson(CFG)

    if not sst_data:
        return "", [[0, 0], [0, 0]], aoi_geojson, build_poi_markers(), ""

    arrF = np.array(sst_data["arrF"], dtype=np.float64)
    lats = np.array(sst_data["lats"], dtype=np.float64)
    lons = np.array(sst_data["lons"], dtype=np.float64)

    # Data is already oriented and masked from the fetch callback
    locked = "lock" in (lock_scale or [])
    vmin, vmax = compute_color_bounds(arrF, locked=locked)

    arrF_vis = upsample_visual(arrF, up_factor or 2)
    overlay_url = sst_to_base64_png(arrF_vis, vmin, vmax)
    bounds = [
        [float(np.min(lats)), float(np.min(lons))],
        [float(np.max(lats)), float(np.max(lons))],
    ]

    legend = build_legend_component(vmin, vmax)
    poi_markers = build_poi_markers(arrF, lats, lons)

    return overlay_url, bounds, aoi_geojson, poi_markers, legend


# ---- Callback 3: Click-to-read temperature ----
@app.callback(
    Output("click-marker", "children"),
    Input("sst-map", "clickData"),
    State("sst-store", "data"),
    prevent_initial_call=True,
)
def handle_map_click(click_data, sst_data):
    if not click_data or not sst_data:
        return []

    lat = click_data["latlng"]["lat"]
    lng = click_data["latlng"]["lng"]
    arrF = np.array(sst_data["arrF"], dtype=np.float64)
    lats = np.array(sst_data["lats"], dtype=np.float64)
    lons = np.array(sst_data["lons"], dtype=np.float64)

    # Find nearest grid point
    lat_idx = np.argmin(np.abs(lats - lat))
    lon_idx = np.argmin(np.abs(lons - lng))

    temp = arrF[lat_idx, lon_idx]
    if not np.isfinite(temp):
        return [
            dl.CircleMarker(
                center=[lat, lng],
                radius=6,
                pathOptions={"color": "#e11d48", "weight": 2, "fillOpacity": 0.3},
                children=[dl.Tooltip("No data", permanent=True)],
            )
        ]

    label = f"{int(temp)}°F  ({lat:.2f}°N, {abs(lng):.2f}°W)"
    return [
        dl.CircleMarker(
            center=[lat, lng],
            radius=6,
            pathOptions={"color": "#e11d48", "weight": 2, "fillOpacity": 0.3},
            children=[dl.Tooltip(label, permanent=True, direction="top")],
        )
    ]


if __name__ == "__main__":
    app.run(debug=True, port=8050)
