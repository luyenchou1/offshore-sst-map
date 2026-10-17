"""The Offshore SST "How it works" modal (/sst).

Mirrors Currents (layout/currentsview.py): a dark dbc.Modal opened by the header's
"How it works" button and the sidebar link, and once by itself on a first visit
(the gotone-sst-intro-v1 flag, clientside callback in app.py). It shares the
Currents modal styles (.currents-about*, .conf-* in assets/currents.css).

Facts here were checked on Oct 16, 2026 (docs/sst1-progress-lane-u.md): MUR's
sensors and update history from its ERDDAP metadata, the 0.36 C agreement from
Chin et al. (2017), the magnetic variation range from WMM-2025 (map/declination.py),
Global Fishing Watch's ~3-day delay from its data-availability page. Sprint 2 (Oct 2026)
added the colors (spectrum, presets, Stepped, Breaks), the depth lines, the spot names
and the wider area; their numbers come from the designer's notes (sst-sprint2/design) and
tools/build_contours.py. The coastline (Oct 17, 2026: OpenStreetMap, the fill under it, the
interpolation zoomed in) is described in docs/coast-progress.md and tools/build_land.py. Update
the text if the data source, the color scale or the measure tool changes.
"""

import dash_bootstrap_components as dbc
from dash import html

VERSION = "v1.0"


def _a(text, href):
    return html.A(text, href=href, target="_blank", rel="noopener")


def _h(text):
    return html.H6(text, className="currents-about-h")


def _p(label, *text):
    return html.P(([html.B(label + " ")] if label else []) + list(text))


# (situation, confidence label, css class, what to expect)
_RELIABILITY = [
    ("Open water after a clear day or two", "High", "conf-high",
     "Breaks and eddy edges are where the map shows them. On average MUR agrees with other "
     "global ocean analyses to within about two-thirds of a degree Fahrenheit."),
    ("The newest day or two", "Good", "conf-good",
     "A first version that gets revised over the next few days. The strip under the map "
     "marks these days preliminary."),
    ("After several cloudy days", "Moderate", "conf-moderate",
     "The map is filling in. Breaks look softer and can lag the real water by days. Trust "
     "the pattern, not the exact line."),
    ("Within a mile or two of the beach, inlets, bays, Long Island Sound", "Moderate",
     "conf-moderate",
     "Each 1 km square can mix land and water. Where MUR stops short of the shore, the color "
     "is carried from the nearest water, up to about 3 km, and shown faded; a tap there gives no reading. A "
     "nearby buoy or your own reading is better."),
    ("A calm, sunny afternoon", "Check on the water", "conf-moderate",
     "The map shows the nighttime temperature below the surface film. The top few feet can "
     "read a few degrees warmer by afternoon."),
    ("After a strong wind or a storm", "Changes fast", "conf-moderate",
     "Wind can move or wipe out a break in a few hours. Look at the newest day before you "
     "run."),
    ("OISST weeks (25 km grid)", "Low", "conf-low",
     "Broad pattern only: no sharp breaks and nothing near the coast."),
]


def about_body():
    """The 'How it works' text (also quoted in the sprint report)."""
    return html.Div(
        [
            html.P(
                "Offshore fish follow the water. Tuna, mahi, marlin and the bait they feed on "
                "collect where warm and cool water meet, along the edges of warm eddies, and where "
                "the continental shelf drops into the canyons. GotOne Offshore SST maps the "
                "sea-surface temperature from Cape May, New Jersey to Portland, Maine, east past "
                "Oceanographer Canyon and south to the Gulf Stream, one map for each day, so you can see where those edges are and how they "
                "moved over the past week."),

            _h("What to look for"),
            _p("Temperature breaks.",
               "A break is a line where the temperature changes quickly, a few degrees in a mile "
               "or two. On the map it shows as colors bunched close together. Bait gathers along "
               "breaks and the fish that eat it hunt them. A sharp break that holds in the same "
               "place for several days is usually a better bet than a faint one that wanders."),
            _p("Warm eddies.",
               "Rings of warm Gulf Stream water break off and drift slowly southwest along the "
               "edge of the shelf, a few miles a day, and can last for weeks or months. They show "
               "as a round or oval patch of warmer color. Their edges carry breaks, and where an "
               "eddy pushes warm water up against the shelf or into a canyon the fishing can turn "
               "on."),
            _p("The shelf edge and the canyons.",
               "The bottom drops from about 100 fathoms at the edge of the shelf to thousands of "
               "feet in Hudson, Block, Atlantis, Veatch, Hydrographer and the other canyons. Water "
               "running into that edge brings cooler, richer water up and holds bait. A break "
               "that lines up with the edge or a canyon head is the classic combination. The thin "
               "dark lines on the map are depth lines: 30, 40 and 100 fathoms on the shelf, 500, "
               "1000 and 2000 meters beyond it. The 100-fathom line, a little heavier, is the edge "
               "of the shelf, and the lines crowd together where the canyon walls drop away."),
            _p("Cold water near the beach.",
               "In summer, a few days of southwest wind can bring cold bottom water up along the "
               "New Jersey shore. It shows as a cold strip close to the beach that can appear in "
               "a day or two."),
            _p("Change over the week.",
               "Step through the seven days to see whether a break is holding, moving or fading, "
               "and which way an eddy is drifting."),

            _h("How to use it"),
            _p("Pick a day.",
               "The map opens on the newest day available, usually yesterday. Pick any other day "
               "back to June 2002 in the date box and press Load week; you'll see that day and "
               "the six days before it. The strip under the map always shows the day on the map, "
               "where the data came from and how fresh it is. If that exact week isn't saved, the "
               "map can show the closest saved week, a few days either way, and the strip says "
               "which. A week nobody has opened before has to come from NOAA first, which takes "
               "longer and can fail when NOAA's servers are busy."),
            _p("Step through the week.",
               "Use ◀ and ▶ to move a day at a time, or Play to loop through all seven. "
               "On a phone or tablet these buttons float on the map."),
            _p("Tap for a temperature.",
               "Tap or click the water to read its temperature on the day shown, with its position "
               "and the same spot's temperature on each day of the week. The reading stays as you "
               "step through the days, so you can watch one spot warm or cool."),
            _p("Spots.",
               "The white circles are 32 well-known offshore spots, wrecks, canyons and banks, and "
               "the dashed box is The Dump. Their names appear as you zoom in, the canyons first. "
               "Tap a circle for its name, position and temperature. "
               "Tapping anywhere else, even next to a spot or inside The Dump, reads the water "
               "where you tapped. Use Spots in the sidebar to show or hide them."),
            _p("Measure.",
               "Press Measure and tap two points to get the distance in nautical and statute miles, "
               "the course from the first point to the second and the temperature at each end. "
               "Tapping a spot's circle snaps to it. The course is shown true and magnetic: the "
               "magnetic course uses the World Magnetic Model (WMM-2025) at the first point, and "
               "the variation here runs from about 12 degrees west off Cape May to about 15 west "
               "in the Gulf of Maine. The line stays as you step through the days; press Clear to "
               "remove it."),
            _p("Layers.",
               "Nautical chart shows NOAA's chart: depths, buoys, wrecks and hazards. Bathymetry "
               "shades the depth of the bottom. Both draw under the colors, so lower Color strength "
               "to see them. Fishing activity shows where commercial fishing "
               "boats carrying AIS trackers fished during the same seven days, from Global Fishing "
               "Watch; most recreational boats don't appear, and the newest two or three days are "
               "usually empty because Global Fishing Watch runs about three days behind. Color "
               "strength fades the temperatures so a layer underneath shows through. Depth lines, "
               "under Colors, turns the depth lines off and on."),

            _h("Reading the colors"),
            html.P(
                "The colors run from dark purple for the coldest water through blue, green and yellow "
                "to orange and dark red for the warmest, the order most offshore temperature charts "
                "use. Every degree gets a clearly different color, so a one-degree break shows, and "
                "the colors are solid, so what you see on the map is what the color bar shows. Land "
                "is dark gray so the water stands out. Tap the water for the exact temperature."),
            _p("Auto.",
               "The colors are fitted to this week's water over the whole map: from the cool end to "
               "the warm end, leaving out the coldest and warmest 5%, and never less than 8 degrees "
               "wide. They mean the same thing on every day of the week and don't change as you pan. "
               "Water beyond the range takes the end colors, which the triangles at the ends of the "
               "color bar stand for. Because the range follows the week, the same orange can be "
               "68 °F one week and 76 °F the next: read the color bar or tap before comparing weeks."),
            _p("Presets.",
               "Tuna (62 to 76 °F) spreads the colors over the water tuna anglers work, from the "
               "inshore side of the shelf front to the edges of the warm eddies; the Gulf Stream "
               "itself shows dark red. Marlin (70 to 84 °F) is for the warm water of the Stream and "
               "its rings, and makes a good late-summer tuna range when the canyons run 74 to 80. "
               "Gulf Stream follows the warmest water of the week: the top of its range is the "
               "week's warmest water and it runs 18 degrees below that, so the north wall and the "
               "rings stay on the scale all year. Custom is your own range in 1-degree steps. A fixed "
               "range (Tuna, Marlin or Custom) also lets you compare weeks: the same color is the "
               "same temperature in both. The map remembers your choice on this device."),
            _p("Fit to view.",
               "Spreads the colors over the water on the map right now. Zoom in on an area and press "
               "it to bring out small differences; the range then stays put until you press it again "
               "or pick another range."),
            _p("The color bar.",
               "Under Colors in the sidebar, it shows the range in use and says whether it is Auto, "
               "a preset, your own or fitted to the view. The gray shape above it is how this "
               "week's water is spread over that range: a tall hump means a lot of water at that "
               "temperature."),
            _p("Stepped.",
               "Turns the smooth colors into one band per degree, so you can count the degrees "
               "across a break."),
            _p("Breaks.",
               "Draws a line at every whole degree, heavier every two degrees with a few labels, and "
               "brightens the water where the temperature changes fast. The highlight starts at half "
               "a degree per nautical mile and is brightest at a degree per mile, the rule of thumb "
               "anglers use. MUR smooths fronts a little, so a real break is often sharper than it "
               "looks here. It is off at first because lines and colors together get busy, "
               "especially on a phone."),
            html.P(
                "Temperatures are to a tenth of a degree Fahrenheit. Weeks saved before October 2026 "
                "may still be in whole degrees until they are refreshed. Areas without color are land "
                "or outside the mapped area."),
            _p("Near the coast.",
               "MUR's 1 km grid stops short of the shoreline along beaches and in bays. So that the "
               "colors meet the coastline, the map carries the color of the nearest water up to "
               "about 3 km toward shore and shows it faded, grayer and lighter, because it is an "
               "estimate: narrow bays like Shinnecock and Moriches can run warmer or colder than "
               "the ocean. A tap there says there is no satellite reading for that spot. The "
               "coastline is OpenStreetMap's. Zoomed in, the colors blend smoothly between "
               "MUR's grid points instead of showing 1 km squares."),
            _p("The mapped area.",
               "37.8 to 44.0 degrees north and 76.0 to 64.8 degrees west: Cape May to Portland, east "
               "past Oceanographer Canyon and south far enough to show the Gulf Stream. Weeks saved "
               "before October 2026 cover a smaller area (38.8 to 43.8 north, to about 68.8 west) "
               "until they are refreshed."),

            _h("Where the data comes from"),
            html.P(
                "The map uses MUR (Multi-scale Ultra-high Resolution) sea-surface temperature from "
                "NASA's Jet Propulsion Laboratory, served by NOAA CoastWatch. MUR is a daily map on "
                "a 1 km grid that blends several satellites with buoys and ships. Infrared sensors "
                "(MODIS on NASA's Terra and Aqua satellites, AVHRR on Europe's MetOp-B satellite) "
                "see fine detail but can't see through clouds. A microwave sensor (AMSR2 on Japan's "
                "GCOM-W satellite) sees through clouds but at much coarser detail and not close to "
                "shore. MUR is built from nighttime measurements, so it shows the water just below "
                "the thin surface layer that warms up on a calm, sunny afternoon."),
            _p("Clouds.",
               "Where clouds hide the water from the infrared satellites, MUR fills the gap from "
               "the microwave data, from nearby days and from the surrounding water. Every spot on "
               "the map has a temperature, but under several days of cloud the detail is a smooth "
               "estimate: breaks look softer than they are, or sit where they were a few days "
               "ago."),
            _p("How fresh it is.",
               "MUR's first version of a day comes out about a day later and is revised as more "
               "measurements arrive; the final version replaces it about four days later. So the "
               "newest days can still change a little, and the strip under the map marks them "
               "preliminary."),
            _p("When MUR isn't available.",
               "The map falls back to NOAA's OISST, a daily map on a 25 km grid. It shows the "
               "broad pattern only: no sharp breaks and nothing near the coast. The strip under "
               "the map then says OISST 25 km."),

            _h("How reliable is it?"),
            html.Table(
                [html.Tr([html.Th("Situation"), html.Th("Confidence"), html.Th("What to expect")])]
                + [html.Tr([html.Td(s), html.Td(html.Span(c, className=f"conf {cls}")), html.Td(d)])
                   for s, c, cls, d in _RELIABILITY],
                className="currents-about-table",
            ),

            _h("Sources"),
            html.Ul([
                html.Li(["Sea-surface temperature: MUR SST v4.1, NASA JPL, distributed by PO.DAAC (",
                         _a("podaac.jpl.nasa.gov", "https://podaac.jpl.nasa.gov/dataset/MUR-JPL-L4-GLOB-v4.1"),
                         "), served by NOAA CoastWatch ERDDAP as dataset ",
                         _a("jplMURSST41", "https://coastwatch.pfeg.noaa.gov/erddap/info/jplMURSST41/index.html"),
                         ". ", _a("NOAA CoastWatch", "https://coastwatch.noaa.gov/"), "."]),
                html.Li(["How MUR is made: Chin, Vazquez-Cuervo and Armstrong (2017), \"A multi-scale "
                         "high-resolution analysis of global sea surface temperature,\" Remote Sensing of "
                         "Environment (", _a("doi.org/10.1016/j.rse.2017.07.029",
                                              "https://doi.org/10.1016/j.rse.2017.07.029"),
                         "). MUR is part of ", _a("GHRSST", "https://www.ghrsst.org/"),
                         ", the Group for High Resolution Sea Surface Temperature."]),
                html.Li(["Backup temperatures: NOAA Daily Optimum Interpolation SST (OISST) v2.1, NOAA NCEI (",
                         _a("ncei.noaa.gov", "https://www.ncei.noaa.gov/products/optimum-interpolation-sst"),
                         "), served by NOAA CoastWatch ERDDAP."]),
                html.Li(["Nautical chart: NOAA Office of Coast Survey chart display service (",
                         _a("nauticalcharts.noaa.gov",
                            "https://nauticalcharts.noaa.gov/data/gis-data-and-services.html"), ")."]),
                html.Li(["Bathymetry: ", _a("GEBCO", "https://www.gebco.net/"), "."]),
                html.Li(["Depth lines: NOAA NCEI ",
                         _a("ETOPO 2022", "https://www.ncei.noaa.gov/products/etopo-global-relief-model"),
                         " at 15 arc-seconds (about 460 m), served by NOAA CoastWatch ERDDAP, smoothed a "
                         "little and simplified for the map."]),
                html.Li(["Coastline: ", _a("OpenStreetMap", "https://www.openstreetmap.org/copyright"),
                         " contributors, land polygons from ",
                         _a("osmdata.openstreetmap.de", "https://osmdata.openstreetmap.de/data/land-polygons.html"),
                         " (Open Database License), simplified for the map."]),
                html.Li(["Basemap: Esri Dark Gray Canvas (",
                         _a("esri.com", "https://www.esri.com/"),
                         "; Esri, HERE, Garmin, OpenStreetMap contributors and the GIS user community)."]),
                html.Li(["Fishing activity: Global Fishing Watch, apparent fishing effort (",
                         _a("globalfishingwatch.org",
                            "https://globalfishingwatch.org/dataset-and-code-fishing-effort/"), ")."]),
                html.Li(["Spots: well-known offshore wrecks, canyons, banks and The Dump; positions are "
                         "approximate."]),
                html.Li(["Magnetic variation: the World Magnetic Model, WMM-2025, from NOAA NCEI and the "
                         "British Geological Survey (",
                         _a("ncei.noaa.gov", "https://www.ncei.noaa.gov/products/world-magnetic-model"),
                         "); NOAA's ",
                         _a("calculator", "https://www.ngdc.noaa.gov/geomag/calculators/magcalc.shtml"),
                         " gives it for any position."]),
            ]),
            html.P("For fishing planning and learning, not for navigation. Check the marine forecast "
                   "before you go.", className="currents-about-note"),
            html.P(f"GotOne Offshore SST {VERSION}", className="currents-about-note sst-about-version"),
        ],
        className="currents-about-body",
    )


def build_about_modal():
    return dbc.Modal(
        [
            dbc.ModalHeader(dbc.ModalTitle("About GotOne Offshore SST")),
            dbc.ModalBody(about_body()),
            dbc.ModalFooter(dbc.Button("Got it", id="sst-about-close", color="primary", size="sm")),
        ],
        id="sst-about", is_open=False, size="lg", centered=True,
        scrollable=True, content_class_name="currents-about",
    )
