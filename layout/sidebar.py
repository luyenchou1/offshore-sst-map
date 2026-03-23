"""Dash sidebar with SST controls and animation playback."""

from datetime import date, timedelta

import dash_bootstrap_components as dbc
from dash import dcc, html

from map.pois import get_all_poi_names, get_poi_options


def _section_label(text):
    """Muted uppercase section label for sidebar grouping."""
    return html.Div(
        text,
        className="sidebar-section-label",
        style={
            "fontSize": "0.65rem",
            "fontWeight": "600",
            "color": "#64748b",
            "textTransform": "uppercase",
            "letterSpacing": "0.08em",
            "marginBottom": "0.5rem",
        },
    )


def _divider():
    """Subtle section divider."""
    return html.Hr(
        style={
            "borderColor": "#1e293b",
            "opacity": "0.5",
            "margin": "0.75rem 0",
        }
    )


def build_sidebar():
    today = date.today()
    default_end = today - timedelta(days=4)  # MUR has ~2-day latency

    return dbc.Col(
        [
            # ── Section 1: Data Selection ──
            _section_label("Data"),
            dcc.DatePickerSingle(
                id="end-date-picker",
                date=default_end,
                min_date_allowed=date(2002, 6, 8),
                max_date_allowed=today - timedelta(days=2),
                display_format="MMM D, YYYY",
                style={"width": "100%"},
            ),
            dbc.Button(
                "Fetch SST",
                id="fetch-btn",
                color="primary",
                className="w-100 mt-2",
            ),
            html.Div(
                id="fetch-status",
                className="mt-1",
                style={"fontSize": "0.7rem"},
            ),

            # ── Section 2: Playback (hidden until data loads) ──
            html.Div(
                id="anim-controls",
                style={"display": "none"},
                children=[
                    _divider(),
                    _section_label("Playback"),
                    html.Div(
                        id="day-indicator",
                        className="text-center",
                        style={
                            "fontSize": "0.8rem",
                            "fontWeight": "600",
                            "color": "#e2e8f0",
                            "marginBottom": "0.25rem",
                        },
                    ),
                    dcc.Slider(
                        id="frame-slider",
                        min=0,
                        max=6,
                        step=1,
                        value=6,
                        marks=None,
                    ),
                    html.Div(
                        [
                            dbc.Button(
                                "\u25C0",
                                id="step-back-btn",
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

            # Fishing spot picker
            html.Div(
                [
                    html.Span(
                        "Spots",
                        style={
                            "fontSize": "0.8rem",
                            "fontWeight": "500",
                            "color": "#cbd5e1",
                        },
                    ),
                    html.Span(
                        id="poi-count",
                        style={
                            "fontSize": "0.7rem",
                            "color": "#64748b",
                            "marginLeft": "0.4rem",
                        },
                    ),
                ],
                className="mb-1",
            ),
            dcc.Dropdown(
                id="poi-picker",
                options=get_poi_options(),
                value=get_all_poi_names(),
                multi=True,
                placeholder="Select spots...",
                style={"fontSize": "0.8rem"},
                className="mb-2",
            ),

            # Lock scale + Measure — compact row
            dbc.Checklist(
                id="lock-scale",
                options=[{"label": " Lock scale (30-90\u00b0F)", "value": "lock"}],
                value=[],
                style={"fontSize": "0.8rem"},
                className="mb-2",
            ),
            dbc.Button(
                "Measure",
                id="measure-btn",
                outline=True,
                color="secondary",
                size="sm",
                className="w-100",
            ),
            html.Div(
                id="measure-readout",
                className="mt-2",
                style={"fontSize": "0.75rem"},
            ),

            dcc.Store(id="measure-state", data={"mode": "off", "a": None, "b": None}),
        ],
        width=2,
        className="gotone-sidebar",
        style={
            "padding": "0.75rem 1rem",
            "height": "calc(100vh - 72px)",
            "overflowY": "auto",
        },
    )
