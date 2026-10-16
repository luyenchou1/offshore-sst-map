"""Dash Leaflet map component."""

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import html

# The map fills the page's flex column (.sst-page in assets/sst.css); the
# status strip sits under it. (Was calc(100vh - 72px): the header is 80 px.)


def build_map():
    return dbc.Col(
        [
            html.Div(
                [
                    # Hamburger menu button (mobile only, hidden on desktop via CSS)
                    html.Button(
                        "☰",
                        id="sidebar-open",
                        className="hamburger-btn",
                    ),
                    # First view: assets/sst.js fits the fishing grounds (phones) or the
                    # whole area, then restores the reader's last view.
                    dl.Map(
                        id="sst-map",
                        center=[40.6, -71.6],
                        zoom=7,
                        minZoom=5,
                        maxZoom=12,
                        style={
                            "height": "100%",
                            "width": "100%",
                            "cursor": "crosshair",
                        },
                        children=[
                            # Esri World Light Gray Canvas — keyless light-grey
                            # basemap. Replaced CARTO Positron, which began
                            # requiring an API key (tiles showed "API KEY
                            # REQUIRED" watermarks). Esri tile scheme is {z}/{y}/{x}.
                            dl.TileLayer(
                                url="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}",
                                attribution='Tiles &copy; <a href="https://www.esri.com/">Esri</a> &mdash; Esri, HERE, Garmin, FAO, NOAA, USGS',
                            ),
                            # (The dashed area outline is gone: it ran over land from New
                            # York Harbor to Maine; the colors show the mapped area.)
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
                                    bounds=[[38.80, -74.96], [43.80, -68.80]],
                                    updateWhenZooming=False,  # wait for zoom to finish
                                    updateWhenIdle=True,      # only load after pan/zoom stops
                                    keepBuffer=4,             # keep more off-screen tiles cached
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
                            # The selection (a reading or a spot card) and the measurement
                            # live in separate groups so each keeps a stable layer order
                            # (react-leaflet reuses layers by position; a Tooltip's
                            # className is fixed when it's created).
                            dl.Pane(
                                [dl.LayerGroup(id="measure-layer"),
                                 dl.LayerGroup(id="click-marker")],
                                name="click-pane",
                                style={"zIndex": 500},
                            ),
                        ],
                    ),
                    html.Div(id="legend-container", className="sst-legend-box"),
                    # Measure instructions on the map (the sidebar is a drawer on phones)
                    html.Div(
                        [html.Span(id="sst-map-hint-text"),
                         html.Button("Cancel", id="sst-hint-cancel", className="sst-hint-cancel")],
                        id="sst-map-hint", className="sst-map-hint", style={"display": "none"},
                    ),
                    # Floating day controls (shown when the sidebar is a drawer: phones,
                    # tablets, narrow windows), wired to the same callbacks as the sidebar's.
                    html.Div(
                        [
                            html.Button("\u25C0", id="sst-pb-back", title="Day before",
                                        **{"aria-label": "Day before"}),
                            html.Button("Play", id="sst-pb-play", className="main"),
                            html.Button("\u25B6", id="sst-pb-fwd", title="Next day",
                                        **{"aria-label": "Next day"}),
                            html.Span(id="sst-pb-date", className="sst-pb-date"),
                        ],
                        id="sst-playbar",
                        className="currents-playbar sst-playbar",
                        style={"display": "none"},
                    ),
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
                    "overflow": "hidden",
                },
            ),
            # The day on the map, the source and how fresh it is (filled by
            # assets/sst.js SST.status). Fixed height, so the map never resizes.
            html.Div(
                html.Div("Loading the week\u2026", className="sst-st-l1"),
                id="sst-status-line", className="sst-status-line",
            ),
        ],
        width=10,
        className="map-col",
        style={"padding": "0"},
    )
