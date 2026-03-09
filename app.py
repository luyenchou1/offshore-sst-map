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
from map.tooltips import build_tooltip_geojson

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
                        "Temperatures in °F. Pan/zoom freely — no page reloads.",
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
    State("days-back", "value"),
    prevent_initial_call=True,
)
def fetch_sst_data(n_clicks, days_back):
    target_date = datetime.now(timezone.utc).date() - timedelta(days=days_back - 1)
    try:
        sst = get_sst(target_date, CFG)
        payload = {
            "server": sst["server"],
            "dataset_id": sst["dataset_id"],
            "dataset_title": sst["dataset_title"],
            "var": sst["var"],
            "units": sst["units"],
            "arrF": sst["arrF"].tolist(),
            "lats": sst["lats"].tolist(),
            "lons": sst["lons"].tolist(),
            "date_used": sst.get("date_used", str(target_date)),
        }
        status = dbc.Alert(
            [
                html.Strong(f"{sst['dataset_id']}"),
                f" — {sst['dataset_title']}",
                html.Br(),
                f"Date: {sst.get('date_used', target_date)} | "
                f"Var: {sst['var']} ({sst['units']})",
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
    Output("tooltip-layer", "data"),
    Output("aoi-outline", "data"),
    Output("poi-layer", "children"),
    Output("legend-container", "children"),
    Input("sst-store", "data"),
    Input("lock-scale", "value"),
    Input("upsample-factor", "value"),
    Input("tooltip-density", "value"),
)
def render_map_layers(sst_data, lock_scale, up_factor, tip_density):
    aoi_geojson = build_aoi_geojson(CFG)
    poi_markers = build_poi_markers()

    if not sst_data:
        return "", [[0, 0], [0, 0]], None, aoi_geojson, poi_markers, ""

    arrF = np.array(sst_data["arrF"])
    lats = np.array(sst_data["lats"])
    lons = np.array(sst_data["lons"])

    # Orient, mask AOI, mask land
    arrF, lats, lons = orient_to_leaflet(arrF, lats, lons)
    arrF = mask_aoi_rasterized(arrF, lats, lons, CFG)
    arrF = mask_land_rasterized(arrF, lats, lons)

    # Color bounds (Bug 2 fix applied in compute_color_bounds)
    locked = "lock" in (lock_scale or [])
    vmin, vmax = compute_color_bounds(arrF, locked=locked)

    # Upsample for visual smoothness, then render to PNG
    arrF_vis = upsample_visual(arrF, up_factor or 2)
    overlay_url = sst_to_base64_png(arrF_vis, vmin, vmax)
    bounds = [
        [float(np.min(lats)), float(np.min(lons))],
        [float(np.max(lats)), float(np.max(lons))],
    ]

    # Tooltip GeoJSON + legend
    tooltip_data = build_tooltip_geojson(arrF, lats, lons, tip_density or "Normal")
    legend = build_legend_component(vmin, vmax)

    return overlay_url, bounds, tooltip_data, aoi_geojson, poi_markers, legend


if __name__ == "__main__":
    app.run(debug=True, port=8050)
