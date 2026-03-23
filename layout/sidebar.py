"""Dash sidebar with SST controls and animation playback."""

from datetime import date, timedelta

import dash_bootstrap_components as dbc
from dash import dcc, html

from map.pois import get_all_poi_names, get_poi_options


def build_sidebar():
    today = date.today()
    default_end = today - timedelta(days=4)  # MUR has ~2-day latency

    return dbc.Col(
        [
            html.Label("End date (7-day window)", className="fw-bold"),
            dcc.DatePickerSingle(
                id="end-date-picker",
                date=default_end,
                min_date_allowed=date(2002, 6, 8),
                max_date_allowed=today - timedelta(days=2),
                display_format="MMM D, YYYY",
                className="mb-2",
                style={"width": "100%"},
            ),
            dbc.Button(
                "Fetch SST",
                id="fetch-btn",
                color="primary",
                className="w-100 mt-2",
            ),
            html.Div(id="fetch-status", className="mt-2"),
            # Animation controls — hidden until data is loaded
            html.Div(
                id="anim-controls",
                style={"display": "none"},
                children=[
                    html.Hr(className="my-2"),
                    html.Div(
                        id="day-indicator",
                        className="text-center mb-1",
                        style={"fontSize": "0.85rem", "fontWeight": "600", "color": "#e2e8f0"},
                    ),
                    dcc.Slider(
                        id="frame-slider",
                        min=0,
                        max=6,
                        step=1,
                        value=6,
                        marks={i: str(i + 1) for i in range(7)},
                    ),
                    html.Div(
                        [
                            dbc.Button(
                                "\u25C0",
                                id="step-back-btn",
                                outline=True,
                                color="secondary",
                                size="sm",
                                className="me-1",
                                style={"minWidth": "36px"},
                            ),
                            dbc.Button(
                                "Play",
                                id="play-pause-btn",
                                color="primary",
                                size="sm",
                                className="me-1",
                                style={"minWidth": "50px"},
                            ),
                            dbc.Button(
                                "\u25B6",
                                id="step-fwd-btn",
                                outline=True,
                                color="secondary",
                                size="sm",
                                style={"minWidth": "36px"},
                            ),
                        ],
                        className="d-flex justify-content-center mt-1",
                    ),
                    dcc.Interval(
                        id="anim-interval",
                        interval=1500,
                        disabled=True,
                    ),
                ],
            ),
            html.Hr(className="my-2"),
            # Fishing spot picker
            html.Label("Fishing spots", className="fw-bold mb-1",
                        style={"fontSize": "0.85rem"}),
            dcc.Dropdown(
                id="poi-picker",
                options=get_poi_options(),
                value=get_all_poi_names(),
                multi=True,
                placeholder="Select spots...",
                style={"fontSize": "0.8rem"},
                className="mb-2",
            ),
            dbc.Checklist(
                id="lock-scale",
                options=[{"label": " Lock scale (30-90°F)", "value": "lock"}],
                value=[],
                style={"fontSize": "0.85rem"},
            ),
            html.Hr(className="my-2"),
            # Ruler / measure tool
            dbc.Button(
                "\U0001F4CF Measure",
                id="measure-btn",
                outline=True,
                color="secondary",
                size="sm",
                className="w-100",
            ),
            html.Div(
                id="measure-readout",
                className="mt-2",
                style={"fontSize": "0.8rem"},
            ),
            dcc.Store(id="measure-state", data={"mode": "off", "a": None, "b": None}),
        ],
        width=2,
        className="gotone-sidebar",
        style={
            "padding": "1rem 1.25rem",
            "height": "calc(100vh - 56px)",
            "overflowY": "auto",
        },
    )
