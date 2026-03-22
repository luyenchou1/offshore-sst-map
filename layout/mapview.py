"""Dash Leaflet map component."""

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import html


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
                            "height": "calc(100vh - 80px)",
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
                            ),
                            dl.LayerGroup(id="poi-layer"),
                            dl.LayerGroup(id="click-marker"),
                        ],
                    ),
                    html.Div(id="legend-container"),
                ],
                style={"position": "relative"},
            )
        ],
        width=10,
        style={"padding": "0"},
    )
