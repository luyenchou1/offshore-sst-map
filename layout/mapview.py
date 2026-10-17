"""Dash Leaflet map component."""

import json

import dash_bootstrap_components as dbc
import dash_leaflet as dl
from dash import html

from map.depth import contours_url
from map.land import land_url
from map.pois import ALL_POIS

MAP_HEIGHT = "calc(100vh - 72px)"

# Fishing activity: one line for the legend or the How it works text (lane U displays it).
GFW_NOTE = ("Fishing activity: where commercial fishing boats carrying AIS trackers fished on the "
            "same days, from Global Fishing Watch. Most recreational boats don't show up, and the "
            "last few days can be empty while GFW catches up.")

AOI_TILE_BOUNDS = [[37.80, -76.00], [44.00, -64.80]]   # the SST coverage box (config.json, sprint 2)

# Dark basemap for /sst only (sprint 2): Esri's Dark Gray Canvas, keyless raster tiles ({z}/{y}/{x}), as on
# /currents. Land is dark gray (#414143) and the opaque SST colors are the brightest thing on the map. The place
# names are a separate layer drawn above the SST and the depth lines (pane sst-labels-pane, z 440). The catch
# map keeps the light basemap. Attribution: the services' own copyrightText plus "Powered by Esri".
_ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas"
DARK_BASE = f"{_ESRI}/World_Dark_Gray_Base/MapServer/tile/{{z}}/{{y}}/{{x}}"
DARK_LABELS = f"{_ESRI}/World_Dark_Gray_Reference/MapServer/tile/{{z}}/{{y}}/{{x}}"
ESRI_DARK_ATTRIBUTION = ('Powered by <a href="https://www.esri.com/">Esri</a> &mdash; Esri, HERE, Garmin, '
                         '&copy; OpenStreetMap contributors, and the GIS user community')


# The three optional layers are created only while their box is ticked (app.toggle_layers puts
# them in the holders below), so a hidden layer downloads no tiles.
def gebco_layer():
    return dl.WMSTileLayer(
        id="gebco-layer",
        url="https://wms.gebco.net/mapserv?",
        layers="GEBCO_LATEST",
        format="image/png",
        transparent=True,
        opacity=0.5,
        attribution='&copy; <a href="https://www.gebco.net">GEBCO</a>',
    )


def chart_layer():
    return dl.WMSTileLayer(
        id="contours-layer",
        url="https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/NOAAChartDisplay/MapServer/exts/MaritimeChartService/WMSServer",
        layers="0,1,2,3,4,5,6,7",
        format="image/png",
        transparent=True,
        opacity=0.6,
        version="1.1.1",
        attribution='&copy; <a href="https://www.charts.noaa.gov">NOAA</a>',
    )


def gfw_layer(date_range=None):
    """Global Fishing Watch tiles through our proxy; ?dr= is the week on the map."""
    return dl.TileLayer(
        id="gfw-layer",
        url=f"/api/gfw/{{z}}/{{x}}/{{y}}.png?dr={date_range or ''}",
        opacity=0.7,
        bounds=AOI_TILE_BOUNDS,
        updateWhenZooming=False,  # wait for zoom to finish
        updateWhenIdle=True,      # only load after pan/zoom stops
        keepBuffer=4,             # keep more off-screen tiles cached
        attribution='&copy; <a href="https://globalfishingwatch.org">Global Fishing Watch</a>',
    )


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
                            # Esri Dark Gray Canvas (keyless; was World Light Gray, which
                            # replaced CARTO Positron when that began wanting an API key).
                            dl.TileLayer(url=DARK_BASE, attribution=ESRI_DARK_ATTRIBUTION),
                            # (The dashed area outline is gone: it ran over land from New
                            # York Harbor to Maine; the colors show the mapped area.)
                            # Chart layers render BELOW the SST overlay.
                            # SST is semi-transparent (RGBA PNGs) so chart
                            # features show through, and the SST opacity
                            # slider lets users fade SST to reveal more.
                            dl.Pane(
                                dl.LayerGroup(id="gebco-holder", children=[]),
                                name="gebco-pane",
                                style={"zIndex": 390},
                            ),
                            dl.Pane(
                                dl.LayerGroup(id="contours-holder", children=[]),
                                name="contours-pane",
                                style={"zIndex": 400},
                            ),
                            # Global Fishing Watch fishing effort heatmap
                            dl.Pane(
                                dl.LayerGroup(id="gfw-holder", children=[]),
                                name="gfw-pane",
                                style={"zIndex": 420},
                            ),
                            # The SST, above the chart layers: assets/sstengine.js (window.SSTEngine)
                            # draws the week on its own canvas in this pane (no image overlay since
                            # sprint 2). The empty group only keeps the pane mounted.
                            dl.Pane(
                                dl.LayerGroup(id="sst-engine-holder", children=[]),
                                name="sst-pane",
                                style={"zIndex": 410},
                            ),
                            # Place names above the SST (410), fishing activity (420) and the
                            # depth lines (430, drawn by assets/sstlook.js), below the spots.
                            dl.Pane(
                                dl.TileLayer(url=DARK_LABELS, opacity=0.9),
                                name="sst-labels-pane",
                                style={"zIndex": 440, "pointerEvents": "none"},
                            ),
                            # Custom panes: above chart layers but below
                            # Leaflet's tooltipPane (650) so tooltips render
                            # on top of the markers, not behind them.
                            # (Spot names: pane sst-spotlabel-pane, z 455, assets/sstlook.js.)
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
                    # Legend (render_static_layers) + the fishing-activity note, shown while
                    # that layer is ticked (GFW_NOTE: lane D's text when merged).
                    # #sst-legend-live: the legend drawn by assets/sstlook.js from the SST
                    # engine's range (shown once the engine is on; the server legend then hides).
                    html.Div(
                        [html.Div(id="legend-container"),
                         html.Div(id="sst-legend-live"),
                         html.Div(globals().get("GFW_NOTE") or (
                                      "Fishing activity: where commercial fishing boats carrying AIS "
                                      "trackers fished on the same days, from Global Fishing Watch. "
                                      "Most recreational boats don't show up, and the last few days "
                                      "can be empty while GFW catches up."),
                                  id="sst-gfw-note", className="sst-gfw-note",
                                  style={"display": "none"})],
                        className="sst-legend-box",
                    ),
                    # What assets/sstlook.js needs from the server: the depth contours' URL
                    # (versioned) and the spots' names and positions (for their labels); and
                    # assets/sstland.js the coastline's (OpenStreetMap, versioned).
                    html.Div(id="sst-look-data", style={"display": "none"}, **{
                        "data-contours": contours_url(),
                        "data-land": land_url(),
                        "data-spots": json.dumps([[n, la, lo] for n, la, lo in ALL_POIS]),
                    }),
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
                                        "A week that isn't saved yet can take up to 30 seconds",
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
                # Height: the page's flex column (.sst-page in assets/sst.css), with the
                # status strip under the map. MAP_HEIGHT (72 px; the header is 80) is unused.
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
