"""Offshore SST Map — NJ to MA AOI (Dash + Dash Leaflet)

Daily sea-surface temperature visualization for the offshore corridor
from New York Harbor to Massachusetts. Data from NOAA CoastWatch ERDDAP
(MUR GHRSST preferred, OISST fallback). Temperatures in °F.

Supports 7-day animated windows: pick any end date back to 2002 and
step or auto-play through the week's SST evolution.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone

import dash
import dash_bootstrap_components as dbc
import dash_leaflet as dl
import numpy as np
from dash import Input, Output, State, ctx, dcc, html

from data.convert import upsample_visual
from data.erddap import get_sst_multiday
from data.geo import mask_aoi_rasterized, mask_land_rasterized, orient_to_leaflet
from layout.mapview import build_map
from layout.sidebar import build_sidebar
from map.colorscale import build_legend_component, compute_color_bounds
from map.measure import format_measurement
from map.overlay import sst_to_base64_png
from map.pois import build_aoi_geojson, build_poi_markers

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open("config.json") as f:
    CFG = json.load(f)

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    title="Offshore SST Analyzer",
)
server = app.server  # for gunicorn

app.layout = dbc.Container(
    [
        dbc.Row(
            dbc.Col(
                [
                    html.H4(
                        "Offshore SST Analyzer",
                        className="mt-2 mb-0",
                    ),
                    html.P(
                        "7-day sea-surface temperatures (°F). "
                        "Click map to read temps. Click spots for details.",
                        className="text-muted mb-1",
                        style={"fontSize": "0.8rem"},
                    ),
                ]
            )
        ),
        dbc.Row([build_sidebar(), build_map()]),
        dcc.Store(id="sst-store"),
        dcc.Store(id="click-pos"),
        html.Div(id="fetch-spinner-target", style={"display": "none"}),
        # Auto-fetch SST on page load (fires once after 500ms)
        dcc.Interval(id="auto-fetch", interval=500, max_intervals=1),
    ],
    fluid=True,
    style={"padding": "0 0.5rem"},
)


# ---- Show loading overlay immediately when Fetch is clicked ----
@app.callback(
    Output("map-loading-overlay", "style"),
    Input("fetch-btn", "n_clicks"),
    prevent_initial_call=True,
)
def show_loading_on_fetch(n_clicks):
    return {
        "position": "absolute",
        "top": 0, "left": 0, "right": 0, "bottom": 0,
        "backgroundColor": "rgba(255,255,255,0.7)",
        "display": "flex",
        "alignItems": "center",
        "justifyContent": "center",
        "zIndex": 1000,
    }


# ---- Callback 1: Fetch 7-day SST data ----
@app.callback(
    Output("sst-store", "data"),
    Output("fetch-status", "children"),
    Output("frame-slider", "marks"),
    Output("frame-slider", "value"),
    Output("frame-slider", "max"),
    Output("anim-controls", "style"),
    Output("fetch-spinner-target", "children"),
    Input("fetch-btn", "n_clicks"),
    Input("auto-fetch", "n_intervals"),
    State("end-date-picker", "date"),
    State("lock-scale", "value"),
    prevent_initial_call=True,
)
def fetch_sst_data(n_clicks, n_intervals, end_date_str, lock_scale):

    # Parse the date string from the date picker
    if end_date_str:
        end_date = datetime.strptime(end_date_str[:10], "%Y-%m-%d").date()
    else:
        end_date = date.today() - timedelta(days=4)

    try:
        sst = get_sst_multiday(end_date, CFG)

        # Process each day's 2D slice: orient, mask, convert to PNG
        lats_raw = sst["lats"]
        lons_raw = sst["lons"]

        processed_days = []
        all_finite = []

        for day_data in sst["days"]:
            arrF = day_data["arrF"]
            arrF, lats, lons = orient_to_leaflet(arrF, lats_raw.copy(), lons_raw.copy())
            arrF = mask_aoi_rasterized(arrF, lats, lons, CFG)
            arrF = mask_land_rasterized(arrF, lats, lons)
            processed_days.append({"arrF": arrF, "date": day_data["date"]})
            finite = arrF[np.isfinite(arrF)]
            if finite.size > 0:
                all_finite.append(finite)

        # Use the oriented lats/lons (same for all days)
        # (orient_to_leaflet may flip them, but result is same for every slice)
        lats = processed_days[0]["arrF"]  # dummy — we need the oriented coords
        # Re-orient once to get the correct lat/lon arrays
        _, lats, lons = orient_to_leaflet(
            sst["days"][0]["arrF"], lats_raw.copy(), lons_raw.copy()
        )

        # Compute resolution
        if len(lats) > 1:
            res_km = abs(float(lats[1] - lats[0])) * 111.0
        else:
            res_km = None

        # Unified color bounds across all days
        locked = "lock" in (lock_scale or [])
        if locked:
            vmin, vmax = 30.0, 90.0
        elif all_finite:
            stacked = np.concatenate(all_finite)
            vmin, vmax = compute_color_bounds(
                np.array(stacked, dtype=np.float64), locked=False
            )
        else:
            vmin, vmax = 30.0, 90.0

        # Pre-render each day's PNG and prepare payload
        frames = []
        raw_days = []
        for pd_item in processed_days:
            arrF_vis = upsample_visual(pd_item["arrF"], 2)
            png_url = sst_to_base64_png(arrF_vis, vmin, vmax)
            frames.append(png_url)
            raw_days.append({
                "arrF": pd_item["arrF"].tolist(),
                "date": pd_item["date"],
            })

        bounds = [
            [float(np.min(lats)), float(np.min(lons))],
            [float(np.max(lats)), float(np.max(lons))],
        ]

        payload = {
            "frames": frames,
            "raw_days": raw_days,
            "lats": lats.tolist(),
            "lons": lons.tolist(),
            "bounds": bounds,
            "vmin": vmin,
            "vmax": vmax,
            "res_km": res_km,
            "server": sst["server"],
            "dataset_id": sst["dataset_id"],
            "dataset_title": sst["dataset_title"],
        }

        num_days = len(frames)
        date_start = raw_days[0]["date"]
        date_end = raw_days[-1]["date"]

        # Build slider marks — just day-of-month to avoid overlap
        marks = {}
        for i, rd in enumerate(raw_days):
            d = datetime.strptime(rd["date"], "%Y-%m-%d")
            marks[i] = str(d.day)

        status = html.Div(
            f"MUR 1km \u2022 {num_days} days loaded",
            className="text-success",
            style={"fontSize": "0.75rem", "fontWeight": "500"},
        )

        anim_visible = {"display": "block"}

        return (
            payload, status,
            marks, num_days - 1, num_days - 1,
            anim_visible, "",
        )

    except Exception as e:
        logger.exception("SST fetch failed")
        return (
            dash.no_update,
            dbc.Alert(
                f"Error: {e}",
                color="danger",
                className="py-2 px-3 mb-0",
                style={"fontSize": "0.8rem"},
            ),
            dash.no_update, dash.no_update, dash.no_update,
            dash.no_update, "",
        )


# ---- Callback 2: Render map layers for current frame ----
@app.callback(
    Output("sst-overlay", "url"),
    Output("sst-overlay", "bounds"),
    Output("aoi-outline", "data"),
    Output("poi-layer", "children"),
    Output("legend-container", "children"),
    Output("map-loading-overlay", "style", allow_duplicate=True),
    Input("sst-store", "data"),
    Input("frame-slider", "value"),
    Input("lock-scale", "value"),
    Input("poi-picker", "value"),
    prevent_initial_call="initial_duplicate",
)
def render_map_layers(sst_data, frame_idx, lock_scale, selected_pois):
    aoi_geojson = build_aoi_geojson(CFG)

    hidden = {"display": "none"}

    if not sst_data or "frames" not in sst_data:
        return "", [[0, 0], [0, 0]], aoi_geojson, build_poi_markers(selected=selected_pois), "", dash.no_update

    frame_idx = frame_idx or 0
    num_frames = len(sst_data["frames"])
    if frame_idx >= num_frames:
        frame_idx = num_frames - 1

    # Pre-rendered PNG for this frame
    overlay_url = sst_data["frames"][frame_idx]
    bounds = sst_data["bounds"]

    # Raw data for POI temperature lookups
    arrF = np.array(sst_data["raw_days"][frame_idx]["arrF"], dtype=np.float64)
    lats = np.array(sst_data["lats"], dtype=np.float64)
    lons = np.array(sst_data["lons"], dtype=np.float64)

    # Unified color bounds
    vmin = sst_data["vmin"]
    vmax = sst_data["vmax"]
    res_km = sst_data.get("res_km")

    legend = build_legend_component(vmin, vmax, res_km=res_km)
    poi_markers = build_poi_markers(arrF, lats, lons, selected=selected_pois)

    return overlay_url, bounds, aoi_geojson, poi_markers, legend, hidden


# ---- Callback 3a: Route map clicks (SST reading vs measure) ----
@app.callback(
    Output("click-pos", "data"),
    Output("measure-state", "data"),
    Output("measure-readout", "children"),
    Output("click-marker", "children", allow_duplicate=True),
    Input("sst-map", "clickData"),
    State("measure-state", "data"),
    prevent_initial_call=True,
)
def handle_map_click(click_data, measure):
    if not click_data:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

    lat = click_data["latlng"]["lat"]
    lng = click_data["latlng"]["lng"]

    if measure and measure.get("mode") == "a":
        # Set point A, wait for B
        new_state = {"mode": "b", "a": {"lat": lat, "lng": lng}, "b": None}
        readout = html.Div(
            [
                html.Div("A set — click point B", style={
                    "fontWeight": "600", "color": "#334155",
                }),
                html.Div(f"{lat:.3f}°N, {abs(lng):.3f}°W", style={
                    "fontSize": "0.75rem", "color": "#64748b",
                }),
            ]
        )
        # Show point A marker
        marker = [
            dl.CircleMarker(
                center=[lat, lng], radius=5,
                pathOptions={"color": "#6366f1", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip("A", permanent=True, direction="top",
                                     offset=[0, -8], pane="tooltipPane")],
            )
        ]
        return dash.no_update, new_state, readout, marker

    elif measure and measure.get("mode") == "b":
        # Set point B, compute distance
        a = measure["a"]
        m = format_measurement(a["lat"], a["lng"], lat, lng)
        new_state = {"mode": "done", "a": a, "b": {"lat": lat, "lng": lng}}
        readout = html.Div(
            [
                html.Div(m["label"], style={
                    "fontWeight": "700", "fontSize": "0.9rem", "color": "#1e293b",
                }),
                html.Div(
                    f"A: {a['lat']:.3f}°N, {abs(a['lng']):.3f}°W",
                    style={"fontSize": "0.7rem", "color": "#64748b", "marginTop": "4px"},
                ),
                html.Div(
                    f"B: {lat:.3f}°N, {abs(lng):.3f}°W",
                    style={"fontSize": "0.7rem", "color": "#64748b"},
                ),
            ]
        )
        # Draw line + both markers
        marker = [
            dl.Polyline(
                positions=[[a["lat"], a["lng"]], [lat, lng]],
                pathOptions={"color": "#6366f1", "weight": 2, "dashArray": "6 4"},
            ),
            dl.CircleMarker(
                center=[a["lat"], a["lng"]], radius=5,
                pathOptions={"color": "#6366f1", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip("A", permanent=True, direction="top",
                                     offset=[0, -8], pane="tooltipPane")],
            ),
            dl.CircleMarker(
                center=[lat, lng], radius=5,
                pathOptions={"color": "#6366f1", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip("B", permanent=True, direction="top",
                                     offset=[0, -8], pane="tooltipPane")],
            ),
            # Distance label at midpoint
            dl.CircleMarker(
                center=[(a["lat"] + lat) / 2, (a["lng"] + lng) / 2],
                radius=0,
                pathOptions={"opacity": 0},
                children=[dl.Tooltip(
                    m["label"], permanent=True, direction="top",
                    offset=[0, -12], pane="tooltipPane",
                    className="measure-tooltip",
                )],
            ),
        ]
        return dash.no_update, new_state, readout, marker

    else:
        # Normal SST reading mode
        pos = {"lat": lat, "lng": lng}
        return pos, dash.no_update, dash.no_update, dash.no_update


# ---- Callback 3b: Render click marker (fires on click, frame change, or data change) ----
@app.callback(
    Output("click-marker", "children"),
    Input("click-pos", "data"),
    Input("sst-store", "data"),
    Input("frame-slider", "value"),
    prevent_initial_call=True,
)
def render_click_marker(click_pos, sst_data, frame_idx):
    if not click_pos or not sst_data or "raw_days" not in sst_data:
        return []

    frame_idx = frame_idx or 0
    num_frames = len(sst_data["raw_days"])
    if frame_idx >= num_frames:
        frame_idx = num_frames - 1

    lat = click_pos["lat"]
    lng = click_pos["lng"]
    arrF = np.array(sst_data["raw_days"][frame_idx]["arrF"], dtype=np.float64)
    lats = np.array(sst_data["lats"], dtype=np.float64)
    lons = np.array(sst_data["lons"], dtype=np.float64)

    # Find nearest grid point
    lat_idx = np.argmin(np.abs(lats - lat))
    lon_idx = np.argmin(np.abs(lons - lng))

    temp = arrF[lat_idx, lon_idx]
    day_date = sst_data["raw_days"][frame_idx]["date"]

    if not np.isfinite(temp):
        return [
            dl.CircleMarker(
                center=[lat, lng],
                radius=6,
                pathOptions={"color": "#e11d48", "weight": 2, "fillOpacity": 0.3},
                children=[
                    dl.Tooltip(
                        html.Div(
                            [
                                html.Div("No data", style={
                                    "fontSize": "0.95rem", "fontWeight": "600",
                                    "color": "#888",
                                }),
                                html.Div(f"{lat:.3f}°N, {abs(lng):.3f}°W", style={
                                    "fontSize": "0.7rem", "color": "#999",
                                    "marginTop": "2px",
                                }),
                            ],
                            style={"textAlign": "center"},
                        ),
                        permanent=True, direction="top", offset=[0, -8],
                        className="sst-tooltip",
                    )
                ],
            )
        ]

    return [
        dl.CircleMarker(
            center=[lat, lng],
            radius=6,
            pathOptions={"color": "#e11d48", "weight": 2, "fillOpacity": 0.3},
            children=[
                dl.Tooltip(
                    html.Div(
                        [
                            html.Div(f"{temp:.1f}°F", style={
                                "fontSize": "1.15rem", "fontWeight": "700",
                                "color": "#1e293b", "lineHeight": "1.2",
                            }),
                            html.Div(f"{lat:.3f}°N, {abs(lng):.3f}°W", style={
                                "fontSize": "0.7rem", "color": "#94a3b8",
                                "marginTop": "2px",
                            }),
                        ],
                        style={"textAlign": "center"},
                    ),
                    permanent=True, direction="top", offset=[0, -8],
                    className="sst-tooltip",
                )
            ],
        )
    ]


# ---- Callback 4: Play/Pause toggle ----
@app.callback(
    Output("anim-interval", "disabled"),
    Output("play-pause-btn", "children"),
    Input("play-pause-btn", "n_clicks"),
    State("anim-interval", "disabled"),
    prevent_initial_call=True,
)
def toggle_play_pause(n_clicks, currently_disabled):
    if currently_disabled:
        return False, "\u23F8"  # pause icon
    else:
        return True, "\u25B6 Play"


# ---- Callback 5: Auto-advance frame on interval tick ----
@app.callback(
    Output("frame-slider", "value", allow_duplicate=True),
    Input("anim-interval", "n_intervals"),
    State("frame-slider", "value"),
    State("frame-slider", "max"),
    prevent_initial_call=True,
)
def auto_advance_frame(n_intervals, current_val, max_val):
    if current_val is None or max_val is None:
        return dash.no_update
    next_val = current_val + 1
    if next_val > max_val:
        next_val = 0
    return next_val


# ---- Callback 6: Step forward/back buttons ----
@app.callback(
    Output("frame-slider", "value", allow_duplicate=True),
    Input("step-back-btn", "n_clicks"),
    Input("step-fwd-btn", "n_clicks"),
    State("frame-slider", "value"),
    State("frame-slider", "max"),
    prevent_initial_call=True,
)
def step_frame(back_clicks, fwd_clicks, current_val, max_val):
    if current_val is None or max_val is None:
        return dash.no_update
    triggered = ctx.triggered_id
    if triggered == "step-back-btn":
        return max(0, current_val - 1)
    elif triggered == "step-fwd-btn":
        return min(max_val, current_val + 1)
    return dash.no_update


# ---- Callback 7: Day indicator text ----
@app.callback(
    Output("day-indicator", "children"),
    Input("frame-slider", "value"),
    State("sst-store", "data"),
)
def update_day_indicator(frame_idx, sst_data):
    if not sst_data or "raw_days" not in sst_data:
        return ""
    frame_idx = frame_idx or 0
    num_days = len(sst_data["raw_days"])
    if frame_idx >= num_days:
        frame_idx = num_days - 1
    day_date = sst_data["raw_days"][frame_idx]["date"]
    d = datetime.strptime(day_date, "%Y-%m-%d")
    label = d.strftime("%b %d, %Y")
    return f"{label}  (Day {frame_idx + 1} of {num_days})"


# ---- Callback 8: Measure toggle ----
@app.callback(
    Output("measure-state", "data", allow_duplicate=True),
    Output("measure-btn", "children"),
    Output("measure-btn", "color"),
    Output("measure-btn", "outline"),
    Output("measure-readout", "children", allow_duplicate=True),
    Output("click-marker", "children", allow_duplicate=True),
    Input("measure-btn", "n_clicks"),
    State("measure-state", "data"),
    prevent_initial_call=True,
)
def toggle_measure(n_clicks, measure):
    mode = measure.get("mode", "off") if measure else "off"
    if mode == "off":
        # Activate — waiting for point A
        return (
            {"mode": "a", "a": None, "b": None},
            "\U0001F4CF Measuring...",
            "primary",
            False,
            html.Div("Click point A on the map", style={
                "fontWeight": "500", "color": "#6366f1", "fontSize": "0.8rem",
            }),
            [],  # clear any existing markers
        )
    else:
        # Deactivate
        return (
            {"mode": "off", "a": None, "b": None},
            "\U0001F4CF Measure",
            "secondary",
            True,
            "",
            [],
        )


if __name__ == "__main__":
    app.run(debug=True, port=8050)
