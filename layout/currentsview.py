"""Currents page (/currents): tidal-current flow for eastern Long Island.

Dash renders only the layout and controls. The particle animation, NOAA
station markers and the tide/current chart all run client-side in
assets/currents.js (window.Currents), driven by small clientside callbacks in
app.py — nothing per frame touches Dash or the server.

Three views (the "View" control):
  Today         typical currents for a chosen day's Montauk tides (default)
  Tide cycle    one average Montauk tide cycle at neap/average/spring strength
  Example weeks hour-by-hour model reconstructions of four notable weeks

Data is prebuilt off-Render (tools/build_tide_atlas.py, tools/build_currents.py)
and served from the persistent disk via /api/currents/*. The page reads the
manifest on every request, so newly uploaded data appears without a restart;
until the tide atlas exists it opens on the example weeks.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import dcc, html

from data import currents as currents_data

_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas"
DARK_BASE = f"{_ESRI}/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}"
DARK_LABELS = f"{_ESRI}/World_Dark_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}"
ESRI_ATTRIBUTION = ('Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; '
                    'Esri, HERE, Garmin, FAO, NOAA, USGS')
ET = ZoneInfo("America/New_York")

# Simulated time per real second of playback.
RATES = [("15 min/s", 900), ("30 min/s", 1800), ("1 hr/s", 3600), ("2 hr/s", 7200)]

VIEW_BLURBS = {
    "date": ("Typical currents for this day's actual Montauk tides, hour by hour. "
             "Tide-based only: no wind or storm effects. The chart line for your spot "
             "is NOAA's own prediction."),
    "cycle": ("One average Montauk tide cycle, low → high → low, built from every hour "
              "of May–Oct 2016 in the NECOFS ocean model. Pick the strength: neap tides "
              "run weakest, spring tides strongest."),
}

PANEL_SHOW = {"display": "block"}
PANEL_HIDE = {"display": "none"}


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


def _blurb(text, **kw):
    return html.Div(text, className="currents-blurb", **kw)


def _segmented(id_, options, value):
    """Radio items styled as a compact button group."""
    return dbc.RadioItems(
        id=id_, options=options, value=value, inline=True,
        class_name="currents-seg", input_class_name="btn-check",
        label_class_name="currents-seg-btn", label_checked_class_name="active",
    )


def build_currents_sidebar(manifest, today):
    weeks = manifest.get("weeks", [])
    stations = manifest.get("stations", [])
    atlas = manifest.get("atlas")
    mode = "date" if atlas else "week"            # no atlas yet -> example weeks
    week_options = [{"label": w["title"], "value": w["id"]} for w in weeks]
    station_options = [
        {"label": ("★ " if s.get("featured") else "") + s["label"], "value": s["id"]}
        for s in stations
    ]
    default_station = manifest.get("default_station")
    if not any(o["value"] == default_station for o in station_options):
        default_station = station_options[0]["value"] if station_options else None
    dates = (atlas or {}).get("dates", {})
    presets = (atlas or {}).get("presets") or [
        {"key": "neap", "label": "Neap"}, {"key": "average", "label": "Average"},
        {"key": "spring", "label": "Spring"}]
    strength_options = [
        {"label": p["label"] + (f" {p['range_ft']:.1f} ft" if "range_ft" in p else ""),
         "value": p["key"]} for p in presets]

    return dbc.Col(
        [
            html.Button("✕", id="sidebar-close", className="sidebar-close-btn", style={
                "background": "none", "border": "none", "color": "#94a3b8",
                "fontSize": "1.5rem", "position": "absolute", "top": "0.5rem",
                "right": "0.75rem", "cursor": "pointer", "padding": "0.25rem",
                "lineHeight": "1", "zIndex": "10",
            }),

            # ── Header (non-scrolling: the date picker's calendar must not clip) ──
            html.Div(
                [
                    _section_label("View", first=True),
                    _segmented("currents-mode", [
                        {"label": "Today", "value": "date", "disabled": not atlas},
                        {"label": "Tide cycle", "value": "cycle", "disabled": not atlas},
                        {"label": "Example weeks", "value": "week"},
                    ], mode),

                    html.Div(
                        [
                            html.Div(
                                [
                                    dcc.DatePickerSingle(
                                        id="currents-date", date=today,
                                        min_date_allowed=dates.get("min"),
                                        max_date_allowed=dates.get("max"),
                                        display_format="MMM D, YYYY",
                                        clearable=False, style={"flex": "1 1 auto"},
                                    ),
                                    dbc.Button("Now", id="currents-now", size="sm",
                                               color="light", outline=True,
                                               className="currents-now-btn",
                                               title="Jump to today, right now"),
                                ],
                                className="currents-date-row",
                            ),
                            _blurb(VIEW_BLURBS["date"]),
                        ],
                        id="currents-panel-date",
                        style=PANEL_SHOW if mode == "date" else PANEL_HIDE,
                    ),
                    html.Div(
                        [
                            _segmented("currents-strength", strength_options, "average"),
                            _blurb(VIEW_BLURBS["cycle"]),
                        ],
                        id="currents-panel-cycle",
                        style=PANEL_SHOW if mode == "cycle" else PANEL_HIDE,
                    ),
                    html.Div(
                        [
                            dcc.Dropdown(
                                id="currents-week", options=week_options,
                                value=week_options[0]["value"] if week_options else None,
                                clearable=False, searchable=False,
                                placeholder="No data yet",
                            ),
                            _blurb("", id="currents-lesson"),
                        ],
                        id="currents-panel-week",
                        style=PANEL_SHOW if mode == "week" else PANEL_HIDE,
                    ),
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
                            dbc.Button("Pause" if mode != "date" else "Play",
                                       id="currents-play", color="primary", size="sm",
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
                    _small("Move along the chart to see any moment on the map; click "
                           "or tap it to jump there (pauses playback).",
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
                    _section_label("Spot"),
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
                    _small("Particles trace the current: brighter and warmer means faster. "
                           "Dots are NOAA current stations: teal = flooding, orange = ebbing, "
                           "white = slack. The chart compares Montauk's tide (blue) with the "
                           "selected spot's current (gold, + flood / − ebb); the dotted gold "
                           "line is the model. Hazed water: wind and ocean currents often "
                           "override the tide there."),
                    html.Div(
                        [
                            html.Button("How it works", id="currents-about-open-2",
                                        className="currents-link-btn"),
                            dcc.Link("← SST Map", href="/", style={
                                "fontSize": "0.7rem", "color": "#0183fe"}),
                        ],
                        className="d-flex justify-content-between align-items-center",
                        style={"marginTop": "auto", "paddingTop": "0.75rem"},
                    ),
                ],
                className="sidebar-body",
            ),
        ],
        id="sidebar-col",
        width=2,
        className="gotone-sidebar",
        # Height comes from the page's flex layout (assets/currents.css).
        style={"padding": "1rem 1.25rem", "position": "relative"},
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
                                # Place names above the particle canvas (z 450) and the
                                # low-confidence haze (455, both created by
                                # currents.js), below station dots (470).
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


def _about_body():
    """The 'How it works' explainer. Accuracy figures come from the atlas
    build report (tools/build_tide_atlas.py) - update them when it changes."""
    def h(text):
        return html.H6(text, className="currents-about-h")

    rows = [
        ("The Race, Plum Gut, Fishers Island passes", "High",
         "The tide explains about 90–95% of the flow and the direction is right about 95% "
         "of the time. The map's currents turn about 25 minutes later than NOAA predicts at "
         "The Race (usually within 45 minutes) and within about 15 minutes at Plum Gut, and "
         "peak speeds in the narrowest gaps read about 30% low. For exact slack times and "
         "speeds, use NOAA's numbers in the chart and the line above it."),
        ("Gardiners Bay, the Peconics", "Good", "The tide explains about 80–90% of the flow."),
        ("Block Island Sound, Montauk Point", "Moderate",
         "About 65–80%; a stiff wind shifts it."),
        ("South side of Montauk, open ocean", "Low",
         "About 30–55%. Wind and offshore currents often matter more than the tide here, "
         "so these waters are hazed on the map."),
    ]
    return html.Div(
        [
            html.P(
                "Anyone who fishes the east end of Long Island knows the current rarely does "
                "what the tide chart suggests. At The Race, slack water comes about two and a "
                "half hours after high or low tide at Montauk; at Plum Gut, about an hour and "
                "a half after, so passes less than 10 miles apart turn about an hour apart. "
                "GotOne Currents shows how the water moves around Montauk, The Race, Plum Gut, "
                "Gardiners Bay, Fishers Island and Block Island Sound, and how that relates to "
                "the tide."),
            h("Three views"),
            html.P([html.B("Today. "),
                    "Estimated currents hour by hour for any day from 2024 through 2030, "
                    "opening on today at the current time. The map applies the typical current "
                    "pattern to that day's actual Montauk high and low tides from NOAA. The "
                    "chart shows NOAA's official current prediction for the spot you select, "
                    "with that day's slack and peak times. These estimates come from the tide "
                    "alone: they don't include wind, storms or runoff, so expect differences "
                    "on blowy days."]),
            html.P([html.B("Tide cycle. "),
                    "The average current pattern through one Montauk tide cycle (low → high "
                    "→ low) at neap, average or spring strength. It's built from every hour "
                    "of May–October 2016 in the NECOFS ocean model, about 350 tide cycles. "
                    "Use it to learn how each spot behaves at each stage of the tide."]),
            html.P([html.B("Example weeks. "),
                    "Four weeks reconstructed hour by hour by the NECOFS model, including that "
                    "week's actual weather: a supermoon spring tide (Sep 25 – Oct 2, 2015), "
                    "summer spring and neap tides (Aug 16–23 and Aug 7–14, 2016), and "
                    "Hurricane Sandy (Oct 26 – Nov 2, 2012), when wind and surge pushed the "
                    "water well off the tide-only predictions."]),
            h("How reliable is it?"),
            html.Table(
                [html.Tr([html.Th("Area"), html.Th("Confidence"), html.Th("What to expect")])]
                + [html.Tr([html.Td(a), html.Td(html.Span(c, className=f"conf conf-{c.lower()}")),
                            html.Td(d)]) for a, c, d in rows],
                className="currents-about-table",
            ),
            html.P("These figures compare the typical-cycle views with the model's hour-by-hour "
                   "reconstruction of weeks it wasn't built from, and with a full year of "
                   "NOAA's 2026 predictions. The line above the chart shows the confidence for "
                   "the spot you select.",
                   className="currents-about-note"),
            h("Sources"),
            html.Ul([
                html.Li(["Currents: NECOFS (Northeast Coastal Ocean Forecast System), the FVCOM "
                         "model run by the University of Massachusetts Dartmouth School for "
                         "Marine Science and Technology: an hourly reconstruction of 1978–2016 "
                         "at about 1 km resolution here."]),
                html.Li(["Tides and tidal-current predictions: NOAA CO-OPS, Montauk station "
                         "8510560 and 86 current-prediction stations."]),
            ]),
            html.P("For fishing planning and learning, not for navigation.",
                   className="currents-about-note"),
        ],
        className="currents-about-body",
    )


def build_currents_page():
    manifest = currents_data.read_manifest() or {"weeks": [], "stations": []}
    lessons = [{"id": w["id"], "subtitle": w.get("subtitle", ""), "lesson": w.get("lesson", "")}
               for w in manifest.get("weeks", [])]
    today = datetime.now(ET).date().isoformat()   # Render runs on UTC
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
                    html.Div(
                        [
                            html.Button("How it works", id="currents-about-open",
                                        className="header-nav-link currents-about-link"),
                            dcc.Link("← SST Map", href="/",
                                     className="header-nav-link currents-sst-link"),
                        ],
                        className="currents-header-links",
                    ),
                ],
                className="gotone-header",
            ),
            dbc.Container(
                [
                    dbc.Row([build_currents_sidebar(manifest, today), build_currents_map()]),
                    html.Div(id="sidebar-backdrop", className="sidebar-backdrop"),
                    dcc.Store(id="currents-weeks", data=lessons),
                    dbc.Modal(
                        [
                            dbc.ModalHeader(dbc.ModalTitle("About GotOne Currents")),
                            dbc.ModalBody(_about_body()),
                            dbc.ModalFooter(dbc.Button("Got it", id="currents-about-close",
                                                       color="primary", size="sm")),
                        ],
                        id="currents-about", is_open=False, size="lg", centered=True,
                        scrollable=True, content_class_name="currents-about",
                    ),
                    # Mounting any dcc.Graph makes Dash lazy-load plotly.js; the
                    # chart itself is drawn by currents.js into #currents-chart.
                    dcc.Graph(id="currents-plotly-loader",
                              figure={"data": [], "layout": {"width": 1, "height": 1}},
                              config={"staticPlot": True}, responsive=False,
                              style={"position": "absolute", "width": "1px",
                                     "height": "1px", "overflow": "hidden",
                                     "opacity": 0, "pointerEvents": "none"}),
                    *[html.Div(id=f"currents-sink-{n}", style={"display": "none"})
                      for n in ("week", "depth", "rate", "station", "options", "step",
                                "strength", "date", "now")],
                ],
                fluid=True,
                style={"padding": "0"},
            ),
        ],
        # Flex column filling the visible viewport: header, then map + chart.
        className="currents-page",
    )
