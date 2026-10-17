# Coastline polish: progress log (branch sst2-coast)

Goal (Luyen, Oct 17 2026): zoomed in near the coast (Hampton Bays, zoom 11, Stepped + Breaks) the SST ends in a
staircase of 1 km cells over the basemap's smoother coastline, and the water near shore is flat 1 km blocks. Make
the coast smooth and clean, the way Canyon Runner and Terrafin draw it: SST underneath, a smooth vector coastline
on top.

## Log

### Oct 17, 00:20 to 00:40: findings before building
- The jagged edge is MUR's effective mask: MUR's own land mask plus Natural Earth 10m (`data/geo`). Measured on the
  week ending 2026-10-14 against OpenStreetMap: 210,122 grid cells are masked, 7,950 of them hold some OSM water
  (4,123 cells' worth of water area). Distance from that water to the nearest MUR water cell, in cells (8-connected):
  1 covers 76.8% of it, 2: 90.7%, 3: 94.5%, 4: 96.3%, 10: 99.0%. What 4 cells don't reach is up rivers (Delaware
  above Wilmington, the upper Chesapeake, the Hudson), where ocean values shouldn't be carried anyway.
- 296 MUR water cells are mostly OSM land (islands and points Natural Earth 10m doesn't have).
- Esri Dark Gray Base land is not one flat color: base land #474749 at zoom 8 to 12 (#414143 at 7, #3f3f41 at 5),
  a second, lighter shade #4d4d4f over built-up areas, lighter roads, darker ponds, and water labels baked into the
  tiles. Water is #232227 (#222327 at zoom 5 and 7). Tiles are JPEG. So a flat land fill over the whole box would
  erase ponds, lakes (Sebago, Winnipesaukee, Quabbin), rivers and roads, and show a band wherever it met the lighter
  shade.
- Coastline sources compared over Esri tiles (Hampton Bays z12, Montauk z11, Casco Bay z11, Chesapeake mouth z10):
  OpenStreetMap land polygons sit on Esri's own water edge within about a pixel at zoom 12 (Esri's land/water at
  these zooms matches OSM closely); GSHHG full resolution is 100 to 200 m off in places and stair-stepped at zoom
  12 (WVS shorelines from the 1990s, 93k vertices in the box); Natural Earth 10m is kilometres off (3k vertices);
  NOAA's shoreline (CUSP) is lines only, US only (the box reaches Nova Scotia), so it would need polygon assembly.

### Oct 17, 00:40 to 01:00: the coastline asset
- `tools/build_land.py`: OSM land polygons (osmdata.openstreetmap.de, split, WGS84; 928 MB zip, data date
  2026-10-17) read with pyogrio straight from the zip (bbox filter), unioned, clipped to the box + 0.02 degree:
  1,105,327 vertices. Per level: topology-preserving Douglas-Peucker in an equal-scale plane, small polygons and holes
  dropped, islands under 1 km2 split out as "islets", cut into overlapping pieces (~20 m overlap), quantized,
  delta-encoded JSON, gzip -9. ~40 s per build.
- Sizes tried (whole box, one level): 120 m 94 KB gz, 60 m 148 KB, 40 m 218 KB, 20 m 389 KB, 12 m 517 KB, 8 m 728 KB.
  Varint binary was only 12 to 15% smaller after gzip, so the assets stay JSON like the contours.
- Final levels: 0 = zoom 5 to 9, 90 m, 0.5-degree pieces, one file, 46,974 vertices, **116 KB gz** (loaded with the
  page); 1 = zoom 10, 45 m, 82,610 vertices, 234 KB; 2 = zoom 11, 20 m, 149,427 vertices, 356 KB; 3 = zoom 12,
  7 m, 306,294 vertices, 711 KB; levels 1 to 3 are one file per 1-degree tile (52 each, largest 96 KB), **1.30 MB gz
  in all**, fetched only when a view needs them. Tolerances are about a third of a pixel at each level's zoom.

### Oct 17, 01:00 to 02:00: drawing it
- Where the land is drawn: into the SST canvas, not a separate land canvas filled with the land color. A flat land
  fill over the box (the brief's first idea) would erase Esri's ponds, lakes, rivers, roads and built-up shading, and
  show a band wherever it met the lighter built-up shade. Clearing the SST on land instead shows the basemap as it is,
  works at any Color strength and over the chart layers, and the coastline is still a smooth vector edge above the SST
  and below fishing activity, the depth lines, place names and spots.
- Screenshots then showed (a) thin dark slivers where Esri's coast lies a pixel or two landward of OSM's (Bellport
  Bay and Montauk's north shore at zoom 12): fixed with a 2 px rim of land color from zoom 11, drawn under what's left
  ("destination-over") so it only shows on land; (b) black blobs where OSM has islets Esri leaves out (Casco Bay):
  fixed by painting islands under 1 km2 in land color over the SST instead of clearing them.
- Fill under the coast: plain 8-connected rings with neighbour means, whole cells within 3.5 km (3 MUR cells; none on
  OISST's 25 km cells, whose gap to the shore is a whole cell: How it works says OISST shows nothing near the coast).
  Restricting the fill to OSM water was considered and not needed: the land is cleared anyway. A tap in filled water
  says "No satellite reading this close to shore" (`SSTEngine.nearShore`) instead of "No data here".
- Interpolation: per view and day, bilinear between cell centres with masked neighbours left out by weight; edge at
  weight 0.25 with a 1 px antialiased ramp. In Stepped mode the first version still showed octagonal staircases in
  nearly even water (bands following 0.1 F quantization cell by cell); the bands now come from the field smoothed as
  the isotherms are (sigma 1.3 cells over the cells in view), so band edges are the temperature lines and line up
  with the Breaks lines. Two cached steps per view and day: the values (the bilinear work), then the colors (a palette
  or range change only redoes the colors).
- Performance on the 4x-throttled phone profile (headless Chrome rasters canvas in software, so drawing costs are
  pessimistic): the first version added ~45 ms to a pan's end at zoom 11 (two full-size land canvases, a smoothed
  mask upscale). Fixed by: four levels so each zoom gets about a third of a pixel of detail; 0.25-degree pieces so a
  view only fills what it shows; no rim below zoom 11; the rim stroked from the level below with bevel joins; and each
  day drawn once per view into its own canvas (colors, isotherms, coastline), so crossfade frames only composite.
- Lost the isotherm drawing functions once to a too-wide text replacement while restructuring; caught by the missing
  isotherms in a screenshot, restored from git, and the comp refactor then replaced the separate isotherm cache.

### Measurements (final)
- Phone profile 390x664 @2x, CPU 4x (`node tools/perf_sstengine.mjs --base ... --only coast`):
  - crossfade frame gaps: median 16.7 ms, 90th percentile 16.8 ms (main: median 33 to 50 ms, 90th 50 to 170 ms);
  - a pan ending (moveend to next frame) at zoom 9 to 12: 75 to 90 ms (main at zoom 11: 54 to 57; with Breaks on 90
    to 95 vs 97 now); zoom 7: ~160 ms both;
  - interpolation ~10 ms per view (Stepped included); the coastline alone with its raster 7 to 11 ms at zoom 9 and
    10, 14 to 18 ms at 11 and 12 (Casco Bay z11 17 ms is the only one over 16);
  - fill plan 10 ms once per week, 2 to 3 ms per day; label land grid 68 ms once per week, when idle;
  - a range change (Custom +/-, a preset) to the next frame: 45 ms at zoom 8 (main 42), 69 ms at zoom 11 (main 42):
    the day's picture is redrawn (colors, interpolated colors, coastline) before it is composited.
- Laptop 1440x900 @2x, no throttle: pan end 48 to 62 ms (39 without the coast work), interpolation 7 to 10 ms at
  about CSS-pixel resolution.
- Load, phone profile with a 9 Mbps / 60 ms network: the week is drawn at 4.2 s as on main (4.0 to 4.6 s); level 0
  (118,539 bytes on the wire) is in by then; zooming to Hampton Bays z12 fetches one 14.6 KB tile and draws level 3
  within 0.24 to 0.49 s.

### Tests (all pass)
- `tools/test_sstengine.mjs` (+10 checks: coastline loads, no filled cell ever read (349 checked), filled water drawn
  and filled land cleared, nearShore, SST cleared on OSM land with no isotherm pixel there, the rim at zoom 11, panes
  410/430/440/450/455, interpolation off at 7 and on at 8 and 11 with mid-cell means, Stepped bands at zoom 11).
- `tools/test_sst_look.py` (+5: assets and budgets and hashes, land and water points at every level, the route
  (gzip, ETag, 304, bad names 404), the layout's data-land, no network in map/land.py; How it works copy).
- Unchanged and passing: test_sst_ui (9), test_sst_fetch (27), test_sst_hang (18), test_labstats (24), test_sst_grid
  (12), smoke_sst.mjs (3 sizes, incl. colors under 10 taps at zoom 9, now interpolated), boot_sst_pages.mjs (24/24),
  load_sst_outage.py (3 scenarios).

### Screenshots
- `/Users/lchou/Code/sst-sprint2/shots-coast/` before-*.jpg (main, 41129c2) and after-*.jpg: hampton11 and hampton12
  (laptop, Stepped + Breaks on), montauk10 (off), canyons8 (on), whole7 (off), hampton11phone (on).

### Deploy notes
- Nothing to set on Render: no new environment variable, no new outside call (the coastline is committed static files
  served from memory by `/api/sst/land/<name>.json`; `map/land.py` reads local files only).
- Pages already open keep the old JS until reloaded; the new sstland.js/sstengine.js arrive with the next page load
  (Dash fingerprints assets by mtime). If the land files ever fail to load, the map draws as before (no clearing, no
  fill), with interpolation still on when zoomed in.
- Repo size: `assets/sst/land/` is 157 files, 1.4 MB. Rebuilding (`tools/build_land.py`) rewrites them all (the build
  time is inside each file), so only rebuild when the coastline should change.
- After the deploy, check on a phone: Hampton Bays at zoom 11 and 12 with Stepped + Breaks, Casco Bay at zoom 12
  (islets gray, no black blobs), a tap in Shinnecock Bay ("No satellite reading this close to shore").

