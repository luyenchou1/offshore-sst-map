"""Dash sidebar with SST controls."""

import dash_bootstrap_components as dbc
from dash import dcc, html


def build_sidebar():
    return dbc.Col(
        [
            html.H5("Controls", className="mb-3"),
            html.Label("Days back", className="fw-bold"),
            dcc.Slider(
                id="days-back",
                min=1,
                max=7,
                value=5,
                step=1,
                marks={i: str(i) for i in range(1, 8)},
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
            # Spinner wraps the fetch-status area — Dash automatically shows
            # the spinner while any callback updating fetch-status is running
            dbc.Spinner(
                html.Div(id="fetch-status", className="mt-3"),
                color="primary",
                type="border",
                size="sm",
                spinner_style={"marginTop": "1rem"},
            ),
            html.Hr(),
            html.P(
                "Click anywhere on the map to read SST at that point.",
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
