"""GotOne Labs landing page (labs.gotoneapp.com/): one tile per tool.

Styled after gotoneapp.com (Helvetica Neue, navy #091d50, yellow #ffe000) and
kept plain for anglers: what each tool is for in one line, a real preview
(assets/labs/*.jpg, drawn from the tools' own data by
tools/build_labs_previews.py) and one big button. Styles: assets/labs.css.
"""

from datetime import date

from dash import dcc, html

TOOLS = [
    {
        "href": "/sst",
        "short": "sst",           # gotoneapp.com/sst (Squarespace URL mapping)
        "img": "/assets/labs/sst.jpg",
        "alt": "Sea-surface temperature map from Long Island to the offshore canyons",
        "area": "Cape May to Portland, Maine",
        "title": "Offshore SST",
        "hook": "Find the temperature breaks.",
        "text": ("Daily satellite water temperatures at 1 km detail. Step through any week "
                 "back to 2002 to watch eddies and breaks move, with the canyons and "
                 "offshore spots marked. Tap the map to read a temperature."),
        "button": "Open the SST map",
    },
    {
        "href": "/currents",
        "short": "currents",           # gotoneapp.com/currents (Squarespace URL mapping)
        "img": "/assets/labs/currents.jpg",
        "alt": "Tidal current flow through The Race, Plum Gut and Gardiners Bay",
        "area": "Eastern Long Island",
        "title": "Currents",
        "pill": "New",
        "hook": "The current doesn't follow the tide table.",
        "text": ("See when the water really turns at The Race, Plum Gut, Montauk and "
                 "Gardiners Bay, hour by hour for any day, plus six harbor inlets from "
                 "Lake Montauk to Sag Harbor Cove."),
        "button": "Open Currents",
    },
    {
        "href": "https://striperrecount.gotoneapp.com/",
        "short": "striper-recount",           # gotoneapp.com/striper-recount (Squarespace URL mapping)
        "img": "/assets/labs/recount.jpg",
        "alt": "The Striper Recount title card: Kit and the Striper on a sunburst",
        "area": "Maine to Virginia",
        "title": "The Striper Recount",
        "pill": "New",
        "hook": "NOAA recounted striper fishing. Here's what changed.",
        "text": ("A 4-minute cartoon song and seven short interactives on NOAA's 2026 correction "
                 "to recreational striped bass estimates: the miscounted trips, why fewer fish "
                 "caught means fewer fish, and what happens next."),
        "button": "Watch and explore",
    },
]


def _tile(t):
    kicker = [html.Span(t["area"], className="labs-area")]
    if t.get("pill"):
        kicker.append(html.Span(t["pill"], className="labs-pill"))
    # tools hosted elsewhere (the Striper Recount) are plain links; the app's own pages use dcc.Link
    link = html.A if t["href"].startswith("http") else dcc.Link
    return link(
        [
            html.Div(html.Img(src=t["img"], alt=t["alt"]), className="labs-shot"),
            html.Div(
                [
                    html.Div(kicker, className="labs-kicker"),
                    html.H2(t["title"]),
                    html.P(t["hook"], className="labs-hook"),
                    html.P(t["text"], className="labs-text"),
                    html.Span([t["button"], html.Span(" →", **{"aria-hidden": "true"})],
                              className="labs-btn"),
                ],
                className="labs-body",
            ),
        ],
        href=t["href"],
        className="labs-tile",
    )


def build_labs_page():
    return html.Div(
        [
            html.Header(
                html.Div(
                    [
                        html.A([html.Img(src="/assets/labs/gotone-wordmark.png", alt="GotOne"),
                                html.Span("Labs", className="labs-word")],
                               href="/", className="labs-brand"),
                        html.A("gotoneapp.com", href="https://www.gotoneapp.com",
                               className="labs-toplink"),
                    ],
                    className="labs-wrap labs-topbar",
                ),
                className="labs-top",
            ),
            html.Section(
                html.Div(
                    [
                        html.H1("Try experimental tools from GotOne Labs"),
                        html.P("Data-driven tools and explainers for saltwater anglers, built on public "
                               "data. Free to use, no sign-up required."),
                    ],
                    className="labs-wrap labs-hero-inner",
                ),
                className="labs-hero",
            ),
            html.Main(
                html.Div(
                    [
                        html.Div([_tile(t) for t in TOOLS], className="labs-grid"),
                        html.P("More tools on the way.", className="labs-more"),
                        html.Div(
                            [
                                html.Div(
                                    [
                                        html.H3("About Labs"),
                                        html.P("These tools are experiments: estimates from "
                                               "public data, great for planning a trip and "
                                               "learning the water, not for navigation. Each "
                                               "one explains its sources and how far to trust "
                                               "it."),
                                    ],
                                ),
                                html.Div(
                                    [
                                        html.H3("Made by GotOne"),
                                        html.P(["GotOne is the fishing log that records the "
                                                "tide, water temperature, wind and moon with "
                                                "every catch, so your history shows you what "
                                                "works. ",
                                                html.A("Get the app",
                                                       href="https://www.gotoneapp.com")]),
                                    ],
                                ),
                            ],
                            className="labs-about",
                        ),
                    ],
                    className="labs-wrap",
                ),
                className="labs-main",
            ),
            html.Footer(
                html.Div(
                    f"© {date.today().year} GotOne Media LLC · Visits are counted anonymously: "
                    "no cookies, nothing about you is stored.",
                    className="labs-wrap",
                ),
                className="labs-foot",
            ),
        ],
        className="labs-page",
    )
