"""Dash Leaflet map component."""

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import html

MAP_HEIGHT = "calc(100vh - 72px)"


def build_map():
    return dbc.Col(
        [
            html.Div(
                [
                    # Hamburger menu button (mobile only, hidden on desktop via CSS)
                    html.Button(
                        "\u2630",
                        id="sidebar-open",
                        className="hamburger-btn",
                    ),
                    dl.Map(
                        id="sst-map",
                        center=[41.2, -71.5],
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
                                        "color": "#94a3b8",
                                        "weight": 1.5,
                                        "fillOpacity": 0,
                                        "dashArray": "6 4",
                                    }
                                },
                            ),
                            # Chart layers render BELOW the SST overlay.
                            # SST is semi-transparent (RGBA PNGs) so chart
                            # features show through, and the SST opacity
                            # slider lets users fade SST to reveal more.
                            dl.Pane(
                                dl.WMSTileLayer(
                                    id="gebco-layer",
                                    url="https://wms.gebco.net/mapserv?",
                                    layers="GEBCO_LATEST",
                                    format="image/png",
                                    transparent=True,
                                    opacity=0,
                                    attribution='&copy; <a href="https://www.gebco.net">GEBCO</a>',
                                ),
                                name="gebco-pane",
                                style={"zIndex": 390},
                            ),
                            dl.Pane(
                                dl.WMSTileLayer(
                                    id="contours-layer",
                                    url="https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/NOAAChartDisplay/MapServer/exts/MaritimeChartService/WMSServer",
                                    layers="0,1,2,3,4,5,6,7",
                                    format="image/png",
                                    transparent=True,
                                    opacity=0,
                                    version="1.1.1",
                                    attribution='&copy; <a href="https://www.charts.noaa.gov">NOAA</a>',
                                ),
                                name="contours-pane",
                                style={"zIndex": 400},
                            ),
                            # Global Fishing Watch fishing effort heatmap
                            dl.Pane(
                                dl.TileLayer(
                                    id="gfw-layer",
                                    url="/api/gfw/{z}/{x}/{y}.png",
                                    opacity=0,
                                    attribution='&copy; <a href="https://globalfishingwatch.org">Global Fishing Watch</a>',
                                ),
                                name="gfw-pane",
                                style={"zIndex": 420},
                            ),
                            # SST overlay on top of chart layers
                            dl.Pane(
                                dl.ImageOverlay(
                                    id="sst-overlay",
                                    url="",
                                    bounds=[[0, 0], [0, 0]],
                                    opacity=0.7,
                                    interactive=False,
                                ),
                                name="sst-pane",
                                style={"zIndex": 410},
                            ),
                            # Custom panes: above chart layers but below
                            # Leaflet's tooltipPane (650) so tooltips render
                            # on top of the markers, not behind them.
                            dl.Pane(
                                dl.LayerGroup(id="poi-layer"),
                                name="poi-pane",
                                style={"zIndex": 450},
                            ),
                            dl.Pane(
                                dl.LayerGroup(id="click-marker"),
                                name="click-pane",
                                style={"zIndex": 500},
                            ),
                        ],
                    ),
                    html.Div(id="legend-container"),
                    # Loading overlay on map — shown during all fetches
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
                                        "Loading 7-day SST data...",
                                        style={
                                            "marginTop": "0.75rem",
                                            "fontWeight": "600",
                                            "color": "#334155",
                                            "fontSize": "0.95rem",
                                        },
                                    ),
                                    html.Div(
                                        "This may take up to a minute",
                                        style={
                                            "marginTop": "0.25rem",
                                            "color": "#64748b",
                                            "fontSize": "0.8rem",
                                            "fontStyle": "italic",
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
                className="map-wrapper",
                style={
                    "position": "relative",
                    "height": MAP_HEIGHT,
                    "overflow": "hidden",
                },
            )
        ],
        width=10,
        className="map-col",
        style={"padding": "0"},
    )
