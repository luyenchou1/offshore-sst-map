"""Dash sidebar with SST controls and animation playback."""

from datetime import date, timedelta

import dash_bootstrap_components as dbc
from dash import dcc, html


def build_sidebar():
    today = date.today()
    default_end = today - timedelta(days=4)  # MUR has ~2-day latency

    return dbc.Col(
        [
            html.H5("Controls", className="mb-3"),
            html.Label("End date (7-day window)", className="fw-bold"),
            dcc.DatePickerSingle(
                id="end-date-picker",
                date=default_end,
                min_date_allowed=date(2002, 6, 8),
                max_date_allowed=today - timedelta(days=2),
                display_format="YYYY-MM-DD",
                className="mb-2",
                style={"width": "100%"},
            ),
            html.Hr(),
            dbc.Checklist(
                id="lock-scale",
                options=[{"label": " Lock color scale (30-90°F)", "value": "lock"}],
                value=[],
                className="mb-3",
            ),
            dbc.Button(
                "Fetch SST",
                id="fetch-btn",
                color="primary",
                className="w-100 mt-2",
            ),
            # Spinner wraps the fetch-status area
            dbc.Spinner(
                html.Div(id="fetch-status", className="mt-3"),
                color="primary",
                type="border",
                size="sm",
                spinner_style={"marginTop": "1rem"},
            ),
            # Animation controls — hidden until data is loaded
            html.Div(
                id="anim-controls",
                style={"display": "none"},
                children=[
                    html.Hr(),
                    html.Label("Animation", className="fw-bold mb-1"),
                    html.Div(
                        id="day-indicator",
                        className="text-center mb-2",
                        style={"fontSize": "0.85rem", "fontWeight": "500"},
                    ),
                    dcc.Slider(
                        id="frame-slider",
                        min=0,
                        max=6,
                        step=1,
                        value=6,  # start on the most recent day
                        marks={i: str(i + 1) for i in range(7)},
                    ),
                    html.Div(
                        [
                            dbc.Button(
                                "\u25C0",
                                id="step-back-btn",
                                color="secondary",
                                size="sm",
                                className="me-1",
                                style={"minWidth": "38px"},
                            ),
                            dbc.Button(
                                "\u25B6 Play",
                                id="play-pause-btn",
                                color="primary",
                                size="sm",
                                className="me-1",
                                style={"minWidth": "70px"},
                            ),
                            dbc.Button(
                                "\u25B6",
                                id="step-fwd-btn",
                                color="secondary",
                                size="sm",
                                style={"minWidth": "38px"},
                            ),
                        ],
                        className="d-flex justify-content-center mt-2",
                    ),
                    dcc.Interval(
                        id="anim-interval",
                        interval=1500,
                        disabled=True,
                    ),
                ],
            ),
            html.Hr(),
            html.P(
                "Click the map to read SST at that point. "
                "Hover fishing spots for name and temperature.",
                className="text-muted mb-0",
                style={"fontSize": "0.8rem"},
            ),
        ],
        width=2,
        style={
            "padding": "1.25rem",
            "backgroundColor": "#f8f9fa",
            "height": "calc(100vh - 90px)",
            "overflowY": "auto",
        },
    )
