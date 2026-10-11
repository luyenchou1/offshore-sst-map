"""Currents page (/currents): animated tidal-current flow for eastern Long Island.

Dash renders only the layout and controls. The particle animation, NOAA
station markers and the tide/current chart all run client-side in
assets/currents.js (window.Currents), driven by small clientside callbacks in
app.py — nothing per frame touches Dash or the server.

Data is prebuilt by tools/build_currents.py and served from the persistent
disk via /api/currents/*. Week and station pickers are populated from the
manifest on every request, so newly uploaded weeks appear without a restart.
"""

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import dcc, html

from data import currents as currents_data

_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas"
DARK_BASE = f"{_ESRI}/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}"
DARK_LABELS = f"{_ESRI}/World_Dark_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}"
ESRI_ATTRIBUTION = ('Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; '
                    'Esri, HERE, Garmin, FAO, NOAA, USGS')

# Simulated time per real second of playback.
RATES = [("15 min/s", 900), ("30 min/s", 1800), ("1 hr/s", 3600), ("2 hr/s", 7200)]


def _section_label(text, first=False):
    return html.Div(
        text,
        style={
            "fontSize": "0.65rem", "fontWeight": "600", "color": "#64748b",
            "textTransform": "uppercase", "letterSpacing": "0.08em",
            "marginBottom": "0.5rem", "marginTop": "0" if first else "0.5rem",
        },
    )


def _divider():
    return html.Hr(style={"borderColor": "#334155", "borderWidth": "1px",
                          "opacity": "1", "margin": "1rem 0"})


def _small(text, **style):
    return html.Div(text, style={"fontSize": "0.72rem", "color": "#94a3b8",
                                 "lineHeight": "1.45", **style})


def build_currents_sidebar(manifest):
    weeks = manifest.get("weeks", [])
    stations = manifest.get("stations", [])
    # Short labels (the sidebar is narrow); dates lead the lesson text instead.
    week_options = [{"label": w["title"], "value": w["id"]} for w in weeks]
    station_options = [
        {"label": ("★ " if s.get("featured") else "") + s["label"], "value": s["id"]}
        for s in stations
    ]
    default_station = manifest.get("default_station")
    if not any(o["value"] == default_station for o in station_options):
        default_station = station_options[0]["value"] if station_options else None

    return dbc.Col(
        [
            html.Button("✕", id="sidebar-close", className="sidebar-close-btn", style={
                "background": "none", "border": "none", "color": "#94a3b8",
                "fontSize": "1.5rem", "position": "absolute", "top": "0.5rem",
                "right": "0.75rem", "cursor": "pointer", "padding": "0.25rem",
                "lineHeight": "1", "zIndex": "10",
            }),

            # ── Header (non-scrolling) ──
            html.Div(
                [
                    _section_label("Week", first=True),
                    dcc.Dropdown(
                        id="currents-week", options=week_options,
                        value=week_options[0]["value"] if week_options else None,
                        clearable=False, searchable=False,
                        placeholder="No data yet",
                    ),
                    html.Div(id="currents-lesson", style={
                        "fontSize": "0.74rem", "color": "#cbd5e1", "lineHeight": "1.45",
                        "marginTop": "0.6rem"}),
                ],
                className="sidebar-header",
            ),

            # ── Body (scrolls) ──
            html.Div(
                [
                    _divider(),
                    _section_label("Playback"),
                    html.Div(
                        [
                            dbc.Button("◀", id="currents-step-back", outline=True,
                                       color="secondary", size="sm", title="Back 1 hour",
                                       className="me-1 playback-btn"),
                            dbc.Button("Play", id="currents-play", color="primary", size="sm",
                                       className="me-1 playback-btn playback-btn-main"),
                            dbc.Button("▶", id="currents-step-fwd", outline=True,
                                       color="secondary", size="sm", title="Forward 1 hour",
                                       className="playback-btn"),
                        ],
                        className="d-flex justify-content-center",
                        style={"marginTop": "0.25rem", "marginBottom": "0.5rem"},
                    ),
                    dbc.RadioItems(
                        id="currents-rate",
                        options=[{"label": label, "value": v} for label, v in RATES],
                        value=1800, inline=True,
                        style={"fontSize": "0.72rem"}, className="currents-radio",
                    ),
                    _small("Drag across the chart to scrub through the week.",
                           marginTop="0.35rem"),

                    _divider(),
                    _section_label("Current layer"),
                    dbc.RadioItems(
                        id="currents-depth",
                        options=[{"label": " Surface", "value": "surface"},
                                 {"label": " Depth-averaged", "value": "davg"}],
                        value="surface",
                        style={"fontSize": "0.8rem"}, className="currents-radio",
                    ),
                    _small("Surface is what you feel drifting or trolling; depth-averaged "
                           "is closer to what a jig or bait sees down the column.",
                           marginTop="0.35rem"),

                    _divider(),
                    _section_label("Station"),
                    dcc.Dropdown(
                        id="currents-station", options=station_options,
                        value=default_station, clearable=False,
                        placeholder="Pick a NOAA station",
                    ),
                    _small("Or click a dot on the map. ★ = key spots.", marginTop="0.35rem"),
                    dbc.Checklist(
                        id="currents-options",
                        options=[{"label": " Show NOAA stations", "value": "stations"},
                                 {"label": " Compare model in chart", "value": "model"}],
                        value=["stations", "model"],
                        style={"fontSize": "0.8rem", "marginTop": "0.5rem"},
                    ),

                    _divider(),
                    _section_label("How to read this"),
                    _small("Particles trace the modeled current: brighter and warmer means "
                           "faster. The chart compares Montauk's tide (blue) with the "
                           "selected station's predicted current (gold, + flood / − ebb). "
                           "Watch the badge above the chart: at most spots slack water "
                           "does not happen at high or low tide."),
                    _small("Model: NECOFS / FVCOM, UMass Dartmouth (~1 km mesh — timing "
                           "is good, peak speed in the narrowest gaps runs low). "
                           "Predictions: NOAA CO-OPS.", marginTop="0.5rem"),

                    html.Div(
                        dcc.Link("← SST Map", href="/", style={
                            "fontSize": "0.7rem", "color": "#0183fe"}),
                        style={"marginTop": "auto", "paddingTop": "0.75rem"},
                    ),
                ],
                className="sidebar-body",
            ),
        ],
        id="sidebar-col",
        width=2,
        className="gotone-sidebar",
        style={"padding": "1rem 1.25rem", "height": "calc(100vh - 72px)",
               "position": "relative"},
    )


def build_currents_map():
    lon0, lat0, lon1, lat1 = currents_data.REGION_BBOX
    return dbc.Col(
        html.Div(
            [
                html.Div(
                    [
                        html.Button("☰", id="sidebar-open", className="hamburger-btn"),
                        dl.Map(
                            id="currents-map",
                            # center+zoom, not bounds-only (dash-leaflet's moveend
                            # handler crashes without them; see catchview.py).
                            center=[(lat0 + lat1) / 2, (lon0 + lon1) / 2],
                            zoom=10, minZoom=9, maxZoom=14, zoomSnap=0.25,
                            maxBounds=[[lat0 - 0.25, lon0 - 0.4], [lat1 + 0.25, lon1 + 0.4]],
                            maxBoundsViscosity=0.8,
                            style={"height": "100%", "width": "100%"},
                            children=[
                                dl.TileLayer(url=DARK_BASE, attribution=ESRI_ATTRIBUTION),
                                # Place names above the particle canvas (z 450,
                                # created by currents.js), below station dots (470).
                                dl.Pane(
                                    dl.TileLayer(url=DARK_LABELS, opacity=0.85),
                                    name="currents-labels-pane",
                                    style={"zIndex": 460, "pointerEvents": "none"},
                                ),
                            ],
                        ),
                    ],
                    className="currents-map-box",
                ),
                html.Div(
                    [
                        html.Div(id="currents-badge", className="currents-badge"),
                        html.Div(
                            [
                                html.Div(id="currents-chart", className="currents-chart"),
                                html.Div(id="currents-cursor-layer",
                                         className="currents-cursor-layer"),
                            ],
                            className="currents-chart-area",
                        ),
                    ],
                    className="currents-chart-box",
                ),
            ],
            className="currents-wrapper",
        ),
        width=10,
        className="map-col",
        style={"padding": "0"},
    )


def build_currents_page():
    manifest = currents_data.read_manifest() or {"weeks": [], "stations": []}
    lessons = [{"id": w["id"], "subtitle": w.get("subtitle", ""), "lesson": w.get("lesson", "")}
               for w in manifest.get("weeks", [])]
    return html.Div(
        [
            html.Div(
                [
                    html.Img(src="/assets/gotone-logo.png"),
                    html.Div(
                        [
                            html.H1("Currents"),
                            html.P(
                                "How the tide really moves around Montauk, The Race, "
                                "Plum Gut & Block Island Sound.",
                                className="subtitle",
                            ),
                        ]
                    ),
                    dcc.Link("← SST Map", href="/", className="header-nav-link"),
                ],
                className="gotone-header",
            ),
            dbc.Container(
                [
                    dbc.Row([build_currents_sidebar(manifest), build_currents_map()]),
                    html.Div(id="sidebar-backdrop", className="sidebar-backdrop"),
                    dcc.Store(id="currents-weeks", data=lessons),
                    # Mounting any dcc.Graph makes Dash lazy-load plotly.js; the
                    # chart itself is drawn by currents.js into #currents-chart.
                    dcc.Graph(id="currents-plotly-loader",
                              figure={"data": [], "layout": {"width": 1, "height": 1}},
                              config={"staticPlot": True}, responsive=False,
                              style={"position": "absolute", "width": "1px",
                                     "height": "1px", "overflow": "hidden",
                                     "opacity": 0, "pointerEvents": "none"}),
                    *[html.Div(id=f"currents-sink-{n}", style={"display": "none"})
                      for n in ("week", "depth", "rate", "station", "options", "step")],
                ],
                fluid=True,
                style={"padding": "0"},
            ),
        ]
    )
