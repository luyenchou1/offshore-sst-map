"""Dash Leaflet map component."""

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import html

MAP_HEIGHT = "calc(100vh - 90px)"


def build_map():
    return dbc.Col(
        [
            html.Div(
                [
                    dl.Map(
                        id="sst-map",
                        center=[40.9, -71.5],
                        zoom=7,
                        minZoom=5,
                        maxZoom=12,
                        style={
                            "height": "100%",
                            "width": "100%",
                            "cursor": "crosshair",
                        },
                        children=[
                            dl.TileLayer(
                                url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
                                attribution='&copy; <a href="https://carto.com/">CARTO</a>',
                            ),
                            dl.GeoJSON(
                                id="aoi-outline",
                                options={
                                    "style": {
                                        "color": "#222",
                                        "weight": 2,
                                        "fillOpacity": 0,
                                    }
                                },
                            ),
                            dl.ImageOverlay(
                                id="sst-overlay",
                                url="",
                                bounds=[[0, 0], [0, 0]],
                                opacity=0.78,
                                interactive=False,
                            ),
                            # Custom panes above the overlay pane (z-index 400)
                            # so POIs and click markers receive hover/click events
                            dl.Pane(
                                dl.LayerGroup(id="poi-layer"),
                                name="poi-pane",
                                style={"zIndex": 650},
                            ),
                            dl.Pane(
                                dl.LayerGroup(id="click-marker"),
                                name="click-pane",
                                style={"zIndex": 660},
                            ),
                        ],
                    ),
                    html.Div(id="legend-container"),
                    # Loading overlay on map
                    html.Div(
                        id="map-loading-overlay",
                        children=[
                            html.Div(
                                [
                                    html.Div(
                                        className="spinner-border text-primary",
                                        role="status",
                                        style={"width": "3rem", "height": "3rem"},
                                    ),
                                    html.Div(
                                        "Loading SST data...",
                                        style={
                                            "marginTop": "0.75rem",
                                            "fontWeight": "600",
                                            "color": "#334155",
                                            "fontSize": "0.95rem",
                                        },
                                    ),
                                ],
                                style={
                                    "display": "flex",
                                    "flexDirection": "column",
                                    "alignItems": "center",
                                },
                            )
                        ],
                        style={
                            "position": "absolute",
                            "top": 0,
                            "left": 0,
                            "right": 0,
                            "bottom": 0,
                            "backgroundColor": "rgba(255,255,255,0.7)",
                            "display": "flex",
                            "alignItems": "center",
                            "justifyContent": "center",
                            "zIndex": 1000,
                        },
                    ),
                ],
                # Wrapper: fixed height, clips everything inside
                style={
                    "position": "relative",
                    "height": MAP_HEIGHT,
                    "overflow": "hidden",
                },
            )
        ],
        width=10,
        style={"padding": "0"},
    )
