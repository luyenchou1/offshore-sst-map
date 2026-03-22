"""Dash sidebar with SST controls and animation playback."""

from datetime import date, timedelta

import dash_bootstrap_components as dbc
from dash import dcc, html


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
                        style={"fontSize": "0.85rem", "fontWeight": "600"},
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
                                "\u25B6",
                                id="play-pause-btn",
                                color="primary",
                                size="sm",
                                className="me-1",
                                style={"minWidth": "36px"},
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
            # Layer toggles
            dbc.Checklist(
                id="show-pois",
                options=[{"label": " Fishing spots", "value": "show"}],
                value=["show"],
                className="mb-1",
                style={"fontSize": "0.85rem"},
            ),
            dbc.Checklist(
                id="lock-scale",
                options=[{"label": " Lock scale (30-90°F)", "value": "lock"}],
                value=[],
                style={"fontSize": "0.85rem"},
            ),
        ],
        width=2,
        style={
            "padding": "1rem 1.25rem",
            "backgroundColor": "#f8f9fa",
            "height": "calc(100vh - 90px)",
            "overflowY": "auto",
        },
    )
