"""Independent GotOne catch map page (route: /catches).

Reuses the SST app's Leaflet/branding scaffolding but is fully decoupled from
the SST data path: its own filters (species group, individual species, year),
its own seasonal-migration animation, and its own dcc.Store. Built to merge
back into the SST map later (shares panes, CSS, and data/catches.py).
"""

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import dcc, html
from dash_extensions.javascript import assign

from data.catches import (
    get_all_groups,
    get_group_options,
    get_groups_meta,
    get_species_options,
    get_year_options,
)

# NE corridor AOI (matches the SST map; "expandable" later via config).
AOI_BOUNDS = [[38.80, -74.96], [43.80, -68.80]]
# Default catch-map view: continental US (data is nationwide; AK/HI/outliers
# are reachable by zooming out to minZoom).
CONUS_BOUNDS = [[24.5, -125.0], [49.5, -66.0]]
MAP_HEIGHT = "calc(100vh - 72px)"

# ── Catch rendering (moved off the SST mapview so the SST page stays clean) ──

# Renders both the spot-level catch markers and the heatmap blobs (same layer,
# branched on the `heat` property set clientside).
#   * Catch points: bright fill + a white halo stroke so they pop against the
#     basemap, a dimmed nautical chart, or each other.
#   * Heat blobs: large, soft, stroke-less circles colored by local density; a
#     CSS blur on the pane (toggled in heatmap mode) melts overlapping blobs
#     together into a smooth kernel-density surface.
catches_point_to_layer = assign("""function(feature, latlng) {
    const p = feature.properties || {};
    if (p.heat) {
        return L.circleMarker(latlng, {
            radius: 9,
            stroke: false,
            fillColor: p.color || "#ff4400",
            fillOpacity: 0.3,   // per-point: density builds via overlap
            interactive: false,
        });
    }
    const color = p.color || "#9aa0a6";
    const marker = L.circleMarker(latlng, {
        radius: 5,
        color: "#ffffff",     // white halo -> pops on any background
        weight: 1.6,
        fillColor: color,
        fillOpacity: 0.95,
        opacity: 1,
    });
    const len = (p.length != null && p.length !== "") ? (" · " + p.length + "\\\"") : "";
    const temp = (p.temp != null && p.temp !== "") ? (" · " + p.temp + "°F") : "";
    const date = p.date ? (" · " + p.date) : "";
    marker.bindTooltip((p.species || "") + len + temp + date, {direction: "top"});
    return marker;
}""")


def _section_label(text, first=False):
    return html.Div(
        text,
        className="sidebar-section-label",
        style={
            "fontSize": "0.65rem", "fontWeight": "600", "color": "#64748b",
            "textTransform": "uppercase", "letterSpacing": "0.08em",
            "marginBottom": "0.5rem", "marginTop": "0" if first else "0.5rem",
        },
    )


def _divider():
    return html.Hr(style={"borderColor": "#334155", "borderWidth": "1px",
                          "opacity": "1", "margin": "1rem 0"})


def _legend():
    rows = []
    for g in get_groups_meta():
        rows.append(html.Div(
            [
                html.Span(style={
                    "display": "inline-block", "width": "12px", "height": "12px",
                    "borderRadius": "3px", "backgroundColor": g["color"],
                    "marginRight": "0.4rem", "verticalAlign": "middle",
                }),
                html.Span(g["label"], style={"fontSize": "0.72rem", "color": "#cbd5e1"}),
            ],
            style={"marginBottom": "0.2rem"},
        ))
    return html.Div(rows)


def _opacity_row(label, slider_id, value):
    """A dimmer row: small label + a 0–1 opacity slider (0 = off)."""
    return html.Div(
        [
            html.Div(label, style={
                "fontSize": "0.72rem", "color": "#cbd5e1", "marginBottom": "-0.25rem"}),
            dcc.Slider(
                id=slider_id, min=0, max=1, step=0.05, value=value, marks=None,
                updatemode="drag",  # dim live while dragging
                tooltip={"placement": "bottom"},
            ),
        ],
        style={"marginBottom": "0.5rem"},
    )


def build_catch_sidebar():
    years = get_year_options()
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
                    _section_label("Species", first=True),
                    html.Div(
                        [
                            html.Span("Select all", id="catch-group-select-all", style={
                                "fontSize": "0.7rem", "color": "#0183fe",
                                "cursor": "pointer", "textDecoration": "underline"}),
                            html.Span(" · ", style={"color": "#475569", "fontSize": "0.7rem"}),
                            html.Span("Deselect all", id="catch-group-deselect-all", style={
                                "fontSize": "0.7rem", "color": "#0183fe",
                                "cursor": "pointer", "textDecoration": "underline"}),
                        ],
                        className="mb-1",
                    ),
                    dbc.Checklist(
                        id="catch-group-picker",
                        options=get_group_options(),
                        value=get_all_groups(),
                        style={"fontSize": "0.78rem"},
                        className="poi-checklist",
                    ),
                    html.Div("Specific species (optional)", style={
                        "fontSize": "0.7rem", "color": "#94a3b8", "marginTop": "0.6rem"}),
                    dcc.Dropdown(
                        id="catch-species-picker",
                        options=get_species_options(),
                        value=[],
                        multi=True,
                        placeholder="All in selected groups",
                        className="catch-dropdown",
                        style={"fontSize": "0.78rem"},
                    ),
                ],
                className="sidebar-header",
            ),

            # ── Body (scrollable) ──
            html.Div(
                [
                    _section_label("Time"),
                    html.Div("Years", style={"fontSize": "0.7rem", "color": "#94a3b8"}),
                    dcc.Dropdown(
                        id="catch-year-picker",
                        options=[{"label": str(y), "value": y} for y in years],
                        value=[],
                        multi=True,
                        placeholder="All years",
                        className="catch-dropdown",
                        style={"fontSize": "0.78rem"},
                    ),
                    html.Div("Window", style={
                        "fontSize": "0.7rem", "color": "#94a3b8", "marginTop": "0.6rem"}),
                    dbc.RadioItems(
                        id="catch-grain",
                        options=[
                            {"label": "Season", "value": "season"},
                            {"label": "Month", "value": "month"},
                            {"label": "Week", "value": "week"},
                            {"label": "Day", "value": "day"},
                        ],
                        value="season",
                        inline=True,
                        style={"fontSize": "0.75rem"},
                        className="catch-grain-radio",
                    ),

                    # Playback controls (shown when Window is Month/Week/Day).
                    # Scrub the slider for a static view; press Play to animate.
                    html.Div(
                        id="catch-anim-controls",
                        style={"display": "none"},
                        children=[
                            html.Div(id="catch-window-label", className="text-center", style={
                                "fontSize": "0.8rem", "fontWeight": "600",
                                "color": "#e2e8f0", "marginTop": "0.5rem",
                                "marginBottom": "0.25rem"}),
                            dcc.Slider(id="catch-frame-slider", min=0, max=52, step=1,
                                       value=0, marks=None),
                            html.Div(
                                [
                                    dbc.Button("◀", id="catch-step-back", outline=True,
                                               color="secondary", size="sm",
                                               className="me-1 playback-btn"),
                                    dbc.Button("Play", id="catch-play-pause", color="primary",
                                               size="sm",
                                               className="me-1 playback-btn playback-btn-main"),
                                    dbc.Button("▶", id="catch-step-fwd", outline=True,
                                               color="secondary", size="sm",
                                               className="playback-btn"),
                                ],
                                className="d-flex justify-content-center",
                                style={"marginTop": "0.25rem"},
                            ),
                            dcc.Interval(id="catch-anim-interval", interval=1200, disabled=True),
                        ],
                    ),

                    _divider(),
                    _section_label("Display"),
                    dbc.RadioItems(
                        id="catch-mode",
                        options=[
                            {"label": " Catch points", "value": "points"},
                            {"label": " Heatmap", "value": "heat"},
                        ],
                        value="points",
                        style={"fontSize": "0.78rem"},
                        className="poi-checklist",
                    ),

                    _divider(),
                    _section_label("Layers (drag to dim)"),
                    _opacity_row("Nautical chart", "catch-contours-opacity", 0),
                    _opacity_row("Bathymetry", "catch-gebco-opacity", 0),
                    _opacity_row("Catches", "catch-points-opacity", 1.0),

                    _divider(),
                    _section_label("Legend"),
                    _legend(),
                    html.Div(id="catch-count", className="text-center", style={
                        "fontSize": "0.72rem", "color": "#94a3b8", "marginTop": "0.6rem"}),

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
        style={"padding": "1rem 1.25rem", "height": "calc(100vh - 72px)", "position": "relative"},
    )


def build_catch_map():
    return dbc.Col(
        [
            html.Div(
                [
                    html.Button("☰", id="sidebar-open", className="hamburger-btn"),
                    dl.Map(
                        id="catch-map",
                        center=[39.5, -96.0],  # continental US
                        zoom=4,
                        minZoom=3,
                        maxZoom=12,
                        # Use center+zoom, NOT bounds-only: dash-leaflet's moveend
                        # handler throws ('equals' of undefined) when the map has no
                        # center/zoom, and that crash aborts Leaflet's renderer reset
                        # on zoom — which left a stuck scale() transform that ballooned
                        # the markers. Canvas keeps 24k points fast.
                        preferCanvas=True,
                        style={"height": "100%", "width": "100%"},
                        children=[
                            dl.TileLayer(
                                url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
                                attribution='&copy; <a href="https://carto.com/">CARTO</a>',
                            ),
                            # GEBCO bathymetry (off by default) — bottom structure
                            dl.Pane(
                                dl.WMSTileLayer(
                                    id="catch-gebco-layer",
                                    url="https://wms.gebco.net/mapserv?",
                                    layers="GEBCO_LATEST",
                                    format="image/png",
                                    transparent=True,
                                    opacity=0,
                                    attribution='&copy; <a href="https://www.gebco.net">GEBCO</a>',
                                ),
                                name="catch-gebco-pane",
                                style={"zIndex": 350},  # below overlayPane (400) so catches sit on top
                            ),
                            # NOAA ENC nautical chart (off by default) — shows wind
                            # farm structures, cables, wrecks, depth contours.
                            dl.Pane(
                                dl.WMSTileLayer(
                                    id="catch-contours-layer",
                                    url="https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/NOAAChartDisplay/MapServer/exts/MaritimeChartService/WMSServer",
                                    layers="0,1,2,3,4,5,6,7",
                                    format="image/png",
                                    transparent=True,
                                    opacity=0,
                                    version="1.1.1",
                                    attribution='&copy; <a href="https://www.charts.noaa.gov">NOAA</a>',
                                ),
                                name="catch-contours-pane",
                                style={"zIndex": 360},  # below overlayPane (400) so catches sit on top
                            ),
                            # Catches live in the DEFAULT overlay pane (not a custom
                            # pane): its renderer resets on zoom, so markers stay a
                            # FIXED pixel size. The WMS panes above are <400, so
                            # catches still render on top of the chart.
                            dl.GeoJSON(
                                id="catches-layer",
                                data={"type": "FeatureCollection", "features": []},
                                pointToLayer=catches_point_to_layer,
                            ),
                        ],
                    ),
                ],
                className="map-wrapper",
                style={"position": "relative", "height": MAP_HEIGHT, "overflow": "hidden"},
            )
        ],
        width=10,
        className="map-col",
        style={"padding": "0"},
    )


def build_catch_page():
    return html.Div(
        [
            html.Div(
                [
                    html.Img(src="/assets/gotone-logo.png"),
                    html.Div(
                        [
                            html.H1("Catch Map"),
                            html.P(
                                "GotOne catches by species, location & season. "
                                "Toggle “Animate over season” to watch the run unfold.",
                                className="subtitle",
                            ),
                        ]
                    ),
                ],
                className="gotone-header",
            ),
            dbc.Container(
                [
                    dbc.Row([build_catch_sidebar(), build_catch_map()]),
                    html.Div(id="sidebar-backdrop", className="sidebar-backdrop"),
                    dcc.Store(id="catch-store"),
                    html.Div(id="catch-opacity-sink", style={"display": "none"}),
                ],
                fluid=True,
                style={"padding": "0"},
            ),
        ]
    )
