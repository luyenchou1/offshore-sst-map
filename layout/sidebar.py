"""Dash sidebar with SST controls and animation playback."""

from datetime import date, timedelta

import dash_bootstrap_components as dbc
from dash import dcc, html

from map.pois import get_all_poi_names, get_poi_options

# The date box's button. The load callback in app.py restores its label after each load
# (FETCH_LABEL on lane D's branch): keep the two the same.
LOAD_LABEL = "Load week"


def _section_label(text, first=False):
    """Muted uppercase section label for sidebar grouping (assets/sst.css)."""
    return html.Div(
        text,
        className="sidebar-section-label",
        style={"marginTop": "0" if first else "0.5rem"},
    )


def _divider():
    """Visible section divider on dark bg."""
    return html.Hr(
        style={
            "borderColor": "#334155",
            "borderWidth": "1px",
            "opacity": "1",
            "margin": "1rem 0",
        }
    )


def build_sidebar(labs_href="/"):
    today = date.today()
    default_end = today - timedelta(days=4)  # MUR has ~2-day latency

    return dbc.Col(
        [
            # Mobile: close drawer button (hidden on desktop via CSS)
            html.Button(
                "\u2715",
                id="sidebar-close",
                className="sidebar-close-btn",
                style={
                    "background": "none",
                    "border": "none",
                    "color": "#94a3b8",
                    "fontSize": "1.5rem",
                    "position": "absolute",
                    "top": "0.5rem",
                    "right": "0.75rem",
                    "cursor": "pointer",
                    "padding": "0.25rem",
                    "lineHeight": "1",
                    "zIndex": "10",
                },
            ),

            # ── Sidebar header: non-scrolling, allows calendar overflow ──
            html.Div(
                [
                    _section_label("Week ending", first=True),
                    dcc.DatePickerSingle(
                        id="end-date-picker",
                        date=default_end,
                        min_date_allowed=date(2002, 6, 8),
                        max_date_allowed=today - timedelta(days=2),
                        display_format="MMM D, YYYY",
                        style={"width": "100%"},
                    ),
                    dbc.Button(
                        LOAD_LABEL,
                        id="fetch-btn",
                        color="primary",
                        className="w-100 mt-2 fetch-btn-cta",
                    ),
                    html.Div(
                        id="fetch-status",
                        className="mt-1",
                        style={"fontSize": "0.7rem"},
                    ),
                ],
                className="sidebar-header",
            ),

            # ── Sidebar body: scrollable area for everything else ──
            html.Div(
                [
                    # ── Section 2: Playback (hidden until data loads) ──
                    html.Div(
                        id="anim-controls",
                        style={"display": "none"},
                        children=[
                            _divider(),
                            _section_label("Day on the map"),
                            html.Div(
                                id="day-indicator",
                                className="text-center sst-day-indicator",
                            ),
                            # Dragging the day slider counts as "step" (assets/labs.js)
                            html.Div(
                                dcc.Slider(
                                    id="frame-slider",
                                    min=0,
                                    max=6,
                                    step=1,
                                    value=6,
                                    marks=None,
                                    allow_direct_input=False,
                                    className="sst-day-slider",
                                ),
                                **{"data-labs-event": "step", "data-labs-tool": "sst"},
                            ),
                            html.Div(
                                [
                                    dbc.Button(
                                        "\u25C0",
                                        id="step-back-btn",
                                        title="Day before",
                                        outline=True,
                                        color="secondary",
                                        size="sm",
                                        className="me-1 playback-btn",
                                    ),
                                    dbc.Button(
                                        "Play",
                                        id="play-pause-btn",
                                        color="primary",
                                        size="sm",
                                        className="me-1 playback-btn playback-btn-main",
                                    ),
                                    dbc.Button(
                                        "\u25B6",
                                        id="step-fwd-btn",
                                        title="Next day",
                                        outline=True,
                                        color="secondary",
                                        size="sm",
                                        className="playback-btn",
                                    ),
                                ],
                                className="d-flex justify-content-center",
                                style={"marginTop": "0.25rem"},
                            ),
                            dcc.Interval(
                                id="anim-interval",
                                interval=1500,
                                disabled=True,
                            ),
                        ],
                    ),

                    _divider(),

                    # ── Section 3: Map Tools ──
                    _section_label("Map tools"),

                    # Fishing spot picker (collapsible checklist)
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Span("Spots", className="sst-spots-label"),
                                    html.Span(
                                        " \u00b7 ",
                                        style={"color": "#475569"},
                                    ),
                                    html.Span(
                                        id="poi-count",
                                        style={
                                            "fontSize": "0.8rem",
                                            "color": "#94a3b8",
                                        },
                                    ),
                                ],
                            ),
                            html.Span(
                                "\u25BE",
                                id="poi-chevron",
                                style={
                                    "color": "#94a3b8",
                                    "fontSize": "1.4rem",
                                    "lineHeight": "1",
                                },
                            ),
                        ],
                        id="poi-collapse-toggle",
                        style={
                            "cursor": "pointer",
                            "userSelect": "none",
                            "display": "flex",
                            "justifyContent": "space-between",
                            "alignItems": "center",
                            "padding": "0.35rem 0.5rem",
                            "borderRadius": "6px",
                            "border": "1px solid #334155",
                            "backgroundColor": "#0f1d32",
                        },
                        className="mb-2",
                    ),
                    dbc.Collapse(
                        id="poi-collapse",
                        is_open=False,
                        children=[
                            html.Div(
                                [
                                    html.Button("Show all", id="poi-select-all", className="sst-link-btn"),
                                    html.Span(" \u00b7 ", className="sst-link-sep"),
                                    html.Button("Hide all", id="poi-deselect-all", className="sst-link-btn"),
                                ],
                                className="mb-1 sst-spot-links",
                            ),
                            dbc.Checklist(
                                id="poi-picker",
                                options=get_poi_options(),
                                value=get_all_poi_names(),
                                className="poi-checklist",
                            ),
                        ],
                    ),

                    html.Div(
                        [
                            dbc.Button(
                                "Measure",
                                id="measure-btn",
                                outline=True,
                                color="secondary",
                                size="sm",
                                className="sst-measure-btn",
                            ),
                            dbc.Button(
                                "Clear",
                                id="measure-clear",
                                outline=True,
                                color="secondary",
                                size="sm",
                                className="sst-measure-clear",
                                style={"display": "none"},
                            ),
                        ],
                        className="sst-measure-row mt-2",
                    ),
                    html.Div(
                        id="measure-readout",
                        className="mt-2 measure-readout",
                    ),

                    _divider(),

                    # ── Section 4: Layers & Visualization ──
                    _section_label("Layers"),
                    dbc.Checklist(
                        id="layer-toggles",
                        options=[
                            {"label": " Nautical chart", "value": "contours"},
                            {"label": " Bathymetry", "value": "gebco"},
                            {"label": " Fishing activity", "value": "gfw"},
                        ],
                        value=[],
                        className="mb-2 sst-checks",
                    ),

                    dbc.Checklist(
                        id="lock-scale",
                        options=[{"label": " Same colors every week (30 to 90\u00b0F)", "value": "lock"}],
                        value=[],
                        className="mb-2 sst-checks",
                    ),
                    html.Div(
                        [
                            html.Span("Color strength", className="sst-small-label"),
                            dcc.Slider(
                                id="sst-opacity",
                                min=0.1,
                                max=1.0,
                                step=0.1,
                                value=0.7,
                                marks=None,
                                tooltip={"placement": "bottom"},
                            ),
                        ],
                        className="mt-2",
                    ),
                    # "Was this helpful?" (filled by assets/labs.js; posts to /api/feedback)
                    html.Div(className="labs-helpful", **{"data-tool": "sst"}),
                    # The version moved into How it works (layout/sstabout.py).
                    html.Div(
                        [
                            html.Button("How it works", id="sst-about-open-2",
                                        className="currents-link-btn"),
                            dcc.Link("\u2190 GotOne Labs", href=labs_href,
                                     refresh=labs_href.startswith("http"),
                                     className="sst-sidebar-labs"),
                        ],
                        className="d-flex justify-content-between align-items-center sst-sidebar-foot",
                    ),
                ],
                className="sidebar-body",
            ),

            dcc.Store(id="measure-state", data={"mode": "off", "a": None, "b": None}),
        ],
        id="sidebar-col",
        width=2,
        className="gotone-sidebar",
        # Height comes from the page's flex layout (.sst-page in assets/sst.css).
        style={
            "padding": "1rem 1.25rem",
            "position": "relative",
        },
    )
