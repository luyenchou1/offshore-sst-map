"""Offshore SST Map — NJ to MA AOI (Dash + Dash Leaflet)

Daily sea-surface temperature visualization for the offshore corridor
from New York Harbor to Massachusetts. Data from NOAA CoastWatch ERDDAP
(MUR GHRSST preferred, OISST fallback). Temperatures in °F.

Supports 7-day animated windows: pick any end date back to 2002 and
step or auto-play through the week's SST evolution.
"""

import base64
import io
import os
import json
import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone

import dash
import dash_bootstrap_components as dbc
import dash_leaflet as dl
import numpy as np
from dash import Input, Output, State, ctx, dcc, html

from data.cache import get_cached, is_stale, put_cache
from data.convert import upsample_visual
from data.erddap import get_sst_multiday
from data.geo import mask_aoi_rasterized, mask_land_rasterized, orient_to_leaflet
from layout.mapview import build_map
from layout.sidebar import build_sidebar
from map.colorscale import build_legend_component, compute_color_bounds
from map.measure import format_measurement
from map.overlay import sst_to_base64_png
from map.pois import (
    build_aoi_geojson, build_poi_markers, build_poi_tooltip,
    find_nearest_poi, get_all_poi_names, _lookup_temp,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

with open("config.json") as f:
    CFG = json.load(f)

app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.FLATLY],
    title="GotOne Offshore SST Analyzer",
)
server = app.server  # for gunicorn


# ---- Allow iframe embedding from GotOne website ----
@server.after_request
def set_iframe_headers(response):
    # Allow embedding on gotoneapp.com (Squarespace) and the Render domain
    response.headers["Content-Security-Policy"] = (
        "frame-ancestors 'self' https://www.gotoneapp.com https://gotoneapp.com "
        "https://sst.gotoneapp.com https://offshore-sst-map.onrender.com"
    )
    # Remove X-Frame-Options if set by any middleware (CSP takes precedence)
    response.headers.pop("X-Frame-Options", None)
    return response


# ---- Server-side raw data cache ----
# Raw float arrays are too large (~18 MB) for dcc.Store / browser transport.
# Keep them server-side; click callbacks read from here instead.
_raw_data_cache = {}  # key → {"raw_days": [...], "lats": np.array, "lons": np.array}


def _cache_key(end_date, locked):
    mode = "locked" if locked else "adaptive"
    return f"{end_date}_{mode}"


def _get_raw_data(data_key):
    """Retrieve raw grid data, falling back to disk cache if needed.

    This handles multi-worker gunicorn (each worker has its own memory),
    server restarts, and any other case where in-memory cache is empty.
    """
    if data_key and data_key in _raw_data_cache:
        return _raw_data_cache[data_key]

    if not data_key:
        return None

    # Fall back to disk cache — parse key to get end_date and locked
    # data_key format: "2026-03-25_adaptive" or "2026-03-25_locked"
    logger.info("_get_raw_data: memory miss for %s, trying disk cache", data_key)
    try:
        parts = data_key.rsplit("_", 1)
        if len(parts) != 2:
            logger.warning("_get_raw_data: invalid key format: %s", data_key)
            return None
        end_date_str, mode = parts
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
        locked = mode == "locked"
        cached = get_cached(end_date, locked)
        if cached:
            logger.info("_get_raw_data: disk cache HIT for %s, rebuilding raw data", data_key)
            _, raw_data = _build_payload_from_disk_cache(cached)
            _raw_data_cache[data_key] = raw_data
            logger.info("Raw data loaded from disk cache: %s", data_key)
            return raw_data
        else:
            logger.warning("_get_raw_data: disk cache MISS for %s", data_key)
    except Exception:
        logger.warning("Failed to load raw data from disk for %s", data_key, exc_info=True)

    return None


# ---- Shared helper: build SST payload from raw ERDDAP result ----
def _build_payload(sst: dict, locked: bool) -> dict:
    """Process raw ERDDAP result into the full payload.

    Runs orient → mask_aoi → mask_land → color bounds → pre-render PNGs.
    Returns (store_payload, raw_data) where:
      - store_payload goes to dcc.Store (frames + metadata, ~7 MB)
      - raw_data stays server-side (float arrays for click lookups, ~18 MB)
    """
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

    # Re-orient once to get correct lat/lon arrays
    _, lats, lons = orient_to_leaflet(
        sst["days"][0]["arrF"], lats_raw.copy(), lons_raw.copy()
    )

    # Compute resolution
    res_km = abs(float(lats[1] - lats[0])) * 111.0 if len(lats) > 1 else None

    # Unified color bounds across all days
    if locked:
        vmin, vmax = 30.0, 90.0
    elif all_finite:
        stacked = np.concatenate(all_finite)
        vmin, vmax = compute_color_bounds(
            np.array(stacked, dtype=np.float64), locked=False
        )
    else:
        vmin, vmax = 30.0, 90.0

    # Pre-render each day's PNG
    frames = []
    dates = []
    for pd_item in processed_days:
        arrF_vis = upsample_visual(pd_item["arrF"], 2)
        png_url = sst_to_base64_png(arrF_vis, vmin, vmax)
        frames.append(png_url)
        dates.append(pd_item["date"])

    bounds = [
        [float(np.min(lats)), float(np.min(lons))],
        [float(np.max(lats)), float(np.max(lons))],
    ]

    # Store payload — goes to browser via dcc.Store (~7 MB)
    store_payload = {
        "frames": frames,
        "dates": dates,
        "bounds": bounds,
        "vmin": float(vmin),
        "vmax": float(vmax),
        "res_km": res_km,
        "server": sst["server"],
        "dataset_id": sst["dataset_id"],
        "dataset_title": sst["dataset_title"],
    }

    # Raw data — stays server-side for click lookups
    raw_data = {
        "raw_days": processed_days,  # list of {"arrF": np.array, "date": str}
        "lats": lats,
        "lons": lons,
    }

    return store_payload, raw_data


def _serialize_array(arr: np.ndarray) -> str:
    """Serialize numpy array to base64 string (memory-efficient)."""
    buf = io.BytesIO()
    np.save(buf, arr)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _deserialize_array(s: str) -> np.ndarray:
    """Deserialize base64 string to numpy array."""
    buf = io.BytesIO(base64.b64decode(s))
    return np.load(buf)


def _build_payload_from_disk_cache(cached: dict):
    """Split a disk-cached payload into store_payload + raw_data.

    Supports two formats:
      - New (v2): raw_days[i]["arrF"] is a base64-encoded numpy string
      - Old (v1): raw_days[i]["arrF"] is a nested JSON list of floats
    """
    raw_days = []
    dates = []
    for rd in cached["raw_days"]:
        arr_data = rd["arrF"]
        if isinstance(arr_data, str):
            # v2 format: base64-encoded numpy array
            arrF = _deserialize_array(arr_data)
        else:
            # v1 format: JSON list of floats
            arrF = np.array(arr_data, dtype=np.float64)
        raw_days.append({"arrF": arrF, "date": rd["date"]})
        dates.append(rd["date"])

    lats_data = cached["lats"]
    lons_data = cached["lons"]
    raw_data = {
        "raw_days": raw_days,
        "lats": _deserialize_array(lats_data) if isinstance(lats_data, str) else np.array(lats_data, dtype=np.float64),
        "lons": _deserialize_array(lons_data) if isinstance(lons_data, str) else np.array(lons_data, dtype=np.float64),
    }

    store_payload = {
        "frames": cached["frames"],
        "dates": dates,
        "bounds": cached["bounds"],
        "vmin": cached["vmin"],
        "vmax": cached["vmax"],
        "res_km": cached.get("res_km"),
        "server": cached.get("server", ""),
        "dataset_id": cached.get("dataset_id", ""),
        "dataset_title": cached.get("dataset_title", ""),
    }

    return store_payload, raw_data


app.layout = html.Div(
    [
        # GotOne branded header
        html.Div(
            [
                html.Img(src=app.get_asset_url("gotone-logo.png")),
                html.Div(
                    [
                        html.H1("Offshore SST Analyzer"),
                        html.P(
                            "7-day sea-surface temperatures. "
                            "Click map to read temps. Click spots for details.",
                            className="subtitle",
                        ),
                    ]
                ),
            ],
            className="gotone-header",
        ),
        dbc.Container(
            [
                dbc.Row([build_sidebar(), build_map()]),
                dcc.Store(id="sst-store"),
                dcc.Store(id="click-pos"),
                html.Div(id="fetch-spinner-target", style={"display": "none"}),
                # Auto-fetch SST on page load (fires once after 500ms)
                dcc.Interval(id="auto-fetch", interval=500, max_intervals=1),
            ],
            fluid=True,
            style={"padding": "0"},
        ),
    ]
)


_LOADING_OVERLAY_VISIBLE = {
    "position": "absolute",
    "top": 0, "left": 0, "right": 0, "bottom": 0,
    "backgroundColor": "rgba(255,255,255,0.7)",
    "display": "flex",
    "alignItems": "center",
    "justifyContent": "center",
    "zIndex": 1000,
}
_LOADING_OVERLAY_HIDDEN = {"display": "none"}


# ---- Callback 1a: Show loading overlay immediately on fetch ----
@app.callback(
    Output("fetch-btn", "disabled"),
    Output("fetch-btn", "children"),
    Output("map-loading-overlay", "style"),
    Input("fetch-btn", "n_clicks"),
    Input("auto-fetch", "n_intervals"),
    prevent_initial_call=True,
)
def show_loading_on_fetch(n_clicks, n_intervals):
    return True, "Loading...", _LOADING_OVERLAY_VISIBLE


# ---- Callback 1b: Fetch 7-day SST data ----
@app.callback(
    output=[
        Output("sst-store", "data"),
        Output("fetch-status", "children"),
        Output("frame-slider", "marks"),
        Output("frame-slider", "value"),
        Output("frame-slider", "max"),
        Output("anim-controls", "style"),
        Output("fetch-spinner-target", "children"),
        Output("fetch-btn", "disabled", allow_duplicate=True),
        Output("fetch-btn", "children", allow_duplicate=True),
    ],
    inputs=[
        Input("fetch-btn", "n_clicks"),
        Input("auto-fetch", "n_intervals"),
    ],
    state=[
        State("end-date-picker", "date"),
        State("lock-scale", "value"),
    ],
    prevent_initial_call=True,
)
def fetch_sst_data(n_clicks, n_intervals, end_date_str, lock_scale):

    # Parse the date string from the date picker
    if end_date_str:
        end_date = datetime.strptime(end_date_str[:10], "%Y-%m-%d").date()
    else:
        end_date = date.today() - timedelta(days=4)

    locked = "lock" in (lock_scale or [])
    data_key = _cache_key(end_date, locked)

    try:
        # Check disk cache first
        cached_hit = False
        cached = get_cached(end_date, locked)
        if cached and not is_stale(end_date):
            logger.info("Serving from cache: %s", end_date)
            store_payload, raw_data = _build_payload_from_disk_cache(cached)
            cached_hit = True
        else:
            # Cache miss — fetch from ERDDAP
            logger.info("Cache miss, fetching from ERDDAP: %s", end_date)
            sst = get_sst_multiday(end_date, CFG)
            store_payload, raw_data = _build_payload(sst, locked)

        # Store raw data server-side FIRST — must happen before disk write
        # so click callbacks work even if the disk write crashes or OOMs
        _raw_data_cache[data_key] = raw_data

        if not cached_hit:
            # Write to disk cache in background thread to avoid blocking
            # the callback response. Uses base64-encoded numpy arrays
            # instead of .tolist() to avoid 3x memory spike from Python
            # float objects (critical on Render's 512MB RAM).
            def _write_disk_cache(sp, rd, ed, lk):
                try:
                    disk_payload = dict(sp)
                    disk_payload["raw_days"] = [
                        {"arrF": _serialize_array(day["arrF"]), "date": day["date"]}
                        for day in rd["raw_days"]
                    ]
                    disk_payload["lats"] = _serialize_array(rd["lats"])
                    disk_payload["lons"] = _serialize_array(rd["lons"])
                    put_cache(ed, lk, disk_payload)
                except Exception:
                    logger.warning("Background cache write failed", exc_info=True)

            threading.Thread(
                target=_write_disk_cache,
                args=(store_payload, raw_data, end_date, locked),
                daemon=True,
            ).start()

        # Add data_key to store payload so click callbacks can look up raw data
        store_payload["data_key"] = data_key

        num_days = len(store_payload["frames"])

        # Build slider marks — just day-of-month
        marks = {}
        for i, d_str in enumerate(store_payload["dates"]):
            d = datetime.strptime(d_str, "%Y-%m-%d")
            marks[i] = str(d.day)

        status = html.Div(
            f"MUR 1km \u2022 {num_days} days loaded",
            className="text-success",
            style={"fontSize": "0.75rem", "fontWeight": "500"},
        )

        anim_visible = {"display": "block"}

        return (
            store_payload, status,
            marks, num_days - 1, num_days - 1,
            anim_visible, "",
            False, "Fetch SST",
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
            False, "Fetch SST",
        )


# ---- Callback 2: Render static layers (POIs, legend, AOI, initial overlay) ----
# Fires on data load or POI selection change. Also sets the SST overlay
# on initial data load (clientside callback handles frame changes only).
@app.callback(
    Output("aoi-outline", "data"),
    Output("poi-layer", "children"),
    Output("legend-container", "children"),
    Output("map-loading-overlay", "style", allow_duplicate=True),
    Output("sst-overlay", "url"),
    Output("sst-overlay", "bounds"),
    Input("sst-store", "data"),
    Input("poi-picker", "value"),
    Input("lock-scale", "value"),
    State("frame-slider", "value"),
    prevent_initial_call="initial_duplicate",
)
def render_static_layers(sst_data, selected_pois, lock_scale, frame_idx):
    aoi_geojson = build_aoi_geojson(CFG)
    hidden = {"display": "none"}

    if not sst_data or "frames" not in sst_data:
        return aoi_geojson, build_poi_markers(selected=selected_pois), "", dash.no_update, "", [[0, 0], [0, 0]]

    vmin = sst_data["vmin"]
    vmax = sst_data["vmax"]
    res_km = sst_data.get("res_km")

    legend = build_legend_component(vmin, vmax, res_km=res_km)
    poi_markers = build_poi_markers(selected=selected_pois)

    # Set overlay for current frame
    idx = frame_idx if frame_idx is not None else len(sst_data["frames"]) - 1
    if idx >= len(sst_data["frames"]):
        idx = len(sst_data["frames"]) - 1
    overlay_url = sst_data["frames"][idx]
    overlay_bounds = sst_data["bounds"]

    return aoi_geojson, poi_markers, legend, hidden, overlay_url, overlay_bounds


# ---- Clientside: Swap overlay PNG by frame index (instant, no server trip) ----
# sst-store is State (not Input) — initial overlay is set by render_static_layers.
# This callback only fires on frame slider changes (user interaction / animation).
app.clientside_callback(
    """
    function(frame_idx, sst_data) {
        if (!sst_data || !sst_data.frames) {
            return [window.dash_clientside.no_update, window.dash_clientside.no_update];
        }
        var idx = frame_idx || 0;
        if (idx >= sst_data.frames.length) idx = sst_data.frames.length - 1;
        return [sst_data.frames[idx], sst_data.bounds];
    }
    """,
    Output("sst-overlay", "url", allow_duplicate=True),
    Output("sst-overlay", "bounds", allow_duplicate=True),
    Input("frame-slider", "value"),
    State("sst-store", "data"),
    prevent_initial_call=True,
)


# ---- Clientside: Play/Pause toggle (no server trip) ----
app.clientside_callback(
    """
    function(n_clicks, currently_disabled) {
        if (currently_disabled) return [false, 'Pause'];
        return [true, 'Play'];
    }
    """,
    Output("anim-interval", "disabled"),
    Output("play-pause-btn", "children"),
    Input("play-pause-btn", "n_clicks"),
    State("anim-interval", "disabled"),
    prevent_initial_call=True,
)


# ---- Clientside: Auto-advance frame on interval tick (no server trip) ----
app.clientside_callback(
    """
    function(n_intervals, current_val, max_val) {
        if (current_val == null || max_val == null) return window.dash_clientside.no_update;
        var next_val = current_val + 1;
        if (next_val > max_val) next_val = 0;
        return next_val;
    }
    """,
    Output("frame-slider", "value", allow_duplicate=True),
    Input("anim-interval", "n_intervals"),
    State("frame-slider", "value"),
    State("frame-slider", "max"),
    prevent_initial_call=True,
)


# ---- Clientside: Step forward/back buttons (no server trip) ----
app.clientside_callback(
    """
    function(back_clicks, fwd_clicks, current_val, max_val) {
        if (current_val == null || max_val == null) return window.dash_clientside.no_update;
        var ctx = window.dash_clientside.callback_context;
        if (!ctx.triggered.length) return window.dash_clientside.no_update;
        var triggered_id = ctx.triggered[0].prop_id.split('.')[0];
        if (triggered_id === 'step-back-btn') return Math.max(0, current_val - 1);
        if (triggered_id === 'step-fwd-btn') return Math.min(max_val, current_val + 1);
        return window.dash_clientside.no_update;
    }
    """,
    Output("frame-slider", "value", allow_duplicate=True),
    Input("step-back-btn", "n_clicks"),
    Input("step-fwd-btn", "n_clicks"),
    State("frame-slider", "value"),
    State("frame-slider", "max"),
    prevent_initial_call=True,
)


# ---- Clientside: Day indicator text (no server trip) ----
app.clientside_callback(
    """
    function(frame_idx, sst_data) {
        if (!sst_data || !sst_data.dates) return '';
        var idx = frame_idx || 0;
        var num_days = sst_data.dates.length;
        if (idx >= num_days) idx = num_days - 1;
        var day_date = sst_data.dates[idx];
        var d = new Date(day_date + 'T12:00:00');
        var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        var label = months[d.getMonth()] + ' ' +
                    String(d.getDate()).padStart(2, '0') + ', ' +
                    d.getFullYear();
        return label + '  (Day ' + (idx + 1) + ' of ' + num_days + ')';
    }
    """,
    Output("day-indicator", "children"),
    Input("frame-slider", "value"),
    State("sst-store", "data"),
)


# ---- Callback 3a: Route map clicks (SST reading vs measure vs POI) ----
@app.callback(
    Output("click-pos", "data"),
    Output("measure-state", "data"),
    Output("measure-readout", "children"),
    Output("click-marker", "children", allow_duplicate=True),
    Input("sst-map", "clickData"),
    State("measure-state", "data"),
    State("poi-picker", "value"),
    State("sst-store", "data"),
    State("frame-slider", "value"),
    prevent_initial_call=True,
)
def handle_map_click(click_data, measure, selected_pois, sst_data, frame_idx):
    if not click_data:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

    lat = click_data["latlng"]["lat"]
    lng = click_data["latlng"]["lng"]

    # Check if click is near a POI
    poi = find_nearest_poi(lat, lng, selected=selected_pois)

    # In measure mode, use POI coordinates if clicked near one
    click_lat = poi[1] if poi else lat
    click_lng = poi[2] if poi else lng

    if measure and measure.get("mode") == "a":
        # Set point A, wait for B
        label_a = poi[0] if poi else f"{click_lat:.3f}\u00b0N, {abs(click_lng):.3f}\u00b0W"
        new_state = {"mode": "b", "a": {"lat": click_lat, "lng": click_lng, "label": label_a}, "b": None}
        readout = html.Div(
            [
                html.Div(f"A: {label_a}", style={
                    "fontWeight": "600", "color": "#334155",
                }),
                html.Div("Click point B", style={
                    "fontSize": "0.8rem", "color": "#0183fe", "marginTop": "2px",
                }),
            ]
        )
        marker = [
            dl.CircleMarker(
                center=[click_lat, click_lng], radius=5,
                pathOptions={"color": "#0183fe", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip(
                    f"A: {label_a}" if poi else "A",
                    permanent=True, direction="top",
                    offset=[0, -8], pane="tooltipPane")],
            )
        ]
        return dash.no_update, new_state, readout, marker

    elif measure and measure.get("mode") == "b":
        # Set point B, compute distance
        a = measure["a"]
        label_b = poi[0] if poi else f"{click_lat:.3f}\u00b0N, {abs(click_lng):.3f}\u00b0W"
        m = format_measurement(a["lat"], a["lng"], click_lat, click_lng)
        new_state = {"mode": "done", "a": a, "b": {"lat": click_lat, "lng": click_lng, "label": label_b}}
        readout = html.Div(
            [
                html.Div(m["label"], style={
                    "fontWeight": "700", "fontSize": "0.9rem", "color": "#1e293b",
                }),
                html.Div(
                    f"A: {a.get('label', '')}",
                    style={"fontSize": "0.7rem", "color": "#64748b", "marginTop": "4px"},
                ),
                html.Div(
                    f"B: {label_b}",
                    style={"fontSize": "0.7rem", "color": "#64748b"},
                ),
            ]
        )
        marker = [
            dl.Polyline(
                positions=[[a["lat"], a["lng"]], [click_lat, click_lng]],
                pathOptions={"color": "#0183fe", "weight": 2, "dashArray": "6 4"},
            ),
            dl.CircleMarker(
                center=[a["lat"], a["lng"]], radius=5,
                pathOptions={"color": "#0183fe", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip(
                    f"A: {a.get('label', 'A')}", permanent=True, direction="top",
                    offset=[0, -8], pane="tooltipPane")],
            ),
            dl.CircleMarker(
                center=[click_lat, click_lng], radius=5,
                pathOptions={"color": "#0183fe", "weight": 2, "fillOpacity": 0.4},
                children=[dl.Tooltip(
                    f"B: {label_b}", permanent=True, direction="top",
                    offset=[0, -8], pane="tooltipPane")],
            ),
            dl.CircleMarker(
                center=[(a["lat"] + click_lat) / 2, (a["lng"] + click_lng) / 2],
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
        # Normal mode — show POI info or SST reading
        if poi:
            # Clicked near a POI — show POI tooltip in click-marker layer
            poi_name, poi_lat, poi_lon = poi
            temp = None
            data_key = sst_data.get("data_key") if sst_data else None
            raw = _get_raw_data(data_key) if data_key else None
            if raw:
                fi = min(frame_idx or 0, len(raw["raw_days"]) - 1)
                arrF = raw["raw_days"][fi]["arrF"]
                temp = _lookup_temp(poi_lat, poi_lon, arrF, raw["lats"], raw["lons"])

            tooltip_content = build_poi_tooltip(poi_name, poi_lat, poi_lon, temp)
            marker = [
                dl.CircleMarker(
                    center=[poi_lat, poi_lon],
                    radius=6,
                    pathOptions={"color": "#475569", "weight": 2, "fillOpacity": 0.3},
                    children=[
                        dl.Tooltip(
                            tooltip_content,
                            permanent=True, direction="top",
                            offset=[0, -10], pane="tooltipPane",
                        )
                    ],
                )
            ]
            return dash.no_update, dash.no_update, dash.no_update, marker
        else:
            # SST reading — pass to click-pos store (triggers render_click_marker)
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
    if not click_pos or not sst_data:
        return []

    data_key = sst_data.get("data_key")
    raw = _get_raw_data(data_key) if data_key else None
    if not raw:
        logger.warning("render_click_marker: raw data unavailable for key=%s "
                       "(in_memory=%s, cache_keys=%s)",
                       data_key,
                       data_key in _raw_data_cache if data_key else False,
                       list(_raw_data_cache.keys()))
        return []

    frame_idx = frame_idx or 0
    num_frames = len(raw["raw_days"])
    if frame_idx >= num_frames:
        frame_idx = num_frames - 1

    lat = click_pos["lat"]
    lng = click_pos["lng"]
    arrF = raw["raw_days"][frame_idx]["arrF"]
    lats = raw["lats"]
    lons = raw["lons"]

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
                children=[
                    dl.Tooltip(
                        html.Div(
                            [
                                html.Div("No data", style={
                                    "fontSize": "0.95rem", "fontWeight": "600",
                                    "color": "#888",
                                }),
                                html.Div(f"{lat:.3f}\u00b0N, {abs(lng):.3f}\u00b0W", style={
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
                            html.Div(f"{temp:.1f}\u00b0F", style={
                                "fontSize": "1.15rem", "fontWeight": "700",
                                "color": "#1e293b", "lineHeight": "1.2",
                            }),
                            html.Div(f"{lat:.3f}\u00b0N, {abs(lng):.3f}\u00b0W", style={
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
                "fontWeight": "500", "color": "#0183fe", "fontSize": "0.8rem",
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


# ---- Callback 9: POI count label ----
@app.callback(
    Output("poi-count", "children"),
    Input("poi-picker", "value"),
)
def update_poi_count(selected):
    n = len(selected) if selected else 0
    total = len(get_all_poi_names())
    return f"({n}/{total})"


# ---- Startup pre-warm: populate server-side cache from disk ----
# Only loads from DISK cache (fast, no ERDDAP). Heavy ERDDAP fetches happen
# on first user request. This keeps startup lightweight so Render's health
# check passes quickly.
def _prewarm_cache():
    time.sleep(15)  # Let gunicorn fully start and pass health check
    end_date = date.today() - timedelta(days=4)
    for locked in [False, True]:
        data_key = _cache_key(end_date, locked)
        if data_key in _raw_data_cache:
            continue
        cached = get_cached(end_date, locked)
        if cached and not is_stale(end_date):
            try:
                _, raw_data = _build_payload_from_disk_cache(cached)
                _raw_data_cache[data_key] = raw_data
                logger.info("Pre-warm: loaded from disk cache %s (locked=%s)", end_date, locked)
            except Exception:
                logger.warning("Pre-warm: disk cache parse failed for %s", end_date, exc_info=True)
        else:
            logger.info("Pre-warm: no disk cache for %s (locked=%s), will fetch on first request", end_date, locked)


if not os.environ.get("_SST_PREWARM_STARTED"):
    os.environ["_SST_PREWARM_STARTED"] = "1"
    threading.Thread(target=_prewarm_cache, daemon=True).start()


if __name__ == "__main__":
    app.run(debug=True, port=8050)
