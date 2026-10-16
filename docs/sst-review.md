# Offshore SST review

Reviewed Oct 16, 2026 against the live site (labs.gotoneapp.com/sst) and the code at `dee6295`. Method: read the SST code paths in `app.py`, `layout/`, `map/`, `data/` and the CSS, compared them with Currents (`layout/currentsview.py`, `assets/currents.js`, `assets/currents.css`), and drove the live page in headless Chromium (Playwright, default HeadlessChrome user agent, so the Labs counter treats every visit as a bot) at 1440x900, 390x664 (touch, 3x) and 820x1180 (touch, 2x). Impact is H/M/L for anglers; effort is S (under a day), M (1 to 3 days), L (a week or more).

## 1. Summary

SST is a capable map built the older way. Every tap, day change and scale change is a Dash round trip to the single server worker; the page says almost nothing about what it shows, how fresh it is or where it's unreliable; and the phone and iPad layouts are the desktop layout squeezed. Currents, by comparison, explains itself on the first visit (How it works), puts the time and state of the map in a strip under it, runs every interaction in the browser instantly, floats its playback controls on the map on touch screens, and is candid about where it can't be trusted (a confidence table, a haze on the map). The biggest SST gaps are three. First, reliability and freshness: both uncached weeks I requested hung for 192 seconds and then showed a wrong error, the OISST fallback can never work, and the page opens four days stale when MUR already has yesterday. Second, explanation: nothing tells an angler what a break or an eddy looks like, what the adaptive colors mean or that clouds are filled in, and on a phone the date isn't on screen at all. Third, reading the map: a readout takes 0.8 s on a laptop and 1.7 s on a phone, spot cards and measurements vanish when the day changes, you can't read a temperature within about 5 nm of any spot or anywhere inside The Dump, and the shelf edge and canyons, the structure anglers read SST against, can't be seen under the colors.

## 2. Findings by area

### 2.1 First visit and explanation

**F1. No How it works and no explanation of what SST is for.** Impact H, effort S.
The header has only "← GotOne Labs". The subtitle ("7-day sea-surface temperatures. Click map to read temps. Click spots for details.") is the only guidance, and phones hide it (`gotone.css` mobile block, `.gotone-header .subtitle { display: none }`). Nothing mentions breaks, eddies, the shelf edge or canyons. Currents opens a 962-word modal 1.6 s after a first visit, with a "How it works" button in the header and a link in the sidebar.
Fix: port the Currents pattern: `dbc.Modal` with the content drafted in section 4, a header button, a sidebar link, first-visit auto-open with a `gotone-sst-intro-v1` localStorage flag, and a `how_it_works` entry in `labstats.EVENTS["sst"]` (counted only on a click, as Currents does).

**F2. No date on the map.** Impact H, effort S.
The day shown lives only in the sidebar ("Oct 12, 2026 (Day 7 of 7)"). On a phone with the drawer closed there is no date anywhere on screen, so a screenshot sent to a fishing group carries no date. Currents keeps time, tide stage and the selected spot's current in `#currents-status-line` under the map.
Fix: a status strip under the map: "Mon, Oct 12, 2026 · day 7 of 7 · satellite blend, 1 km · newest available Oct 15", with a second line for the current reading or spot. Same fixed-height approach as Currents so the map never resizes.

**F3. The first view spends the map on land and hides the canyons on phones.** Impact M, effort S.
`mapview.py:23` opens at zoom 7 on 41.2 N, 71.5 W. On a laptop about half the map is New York, Pennsylvania and Vermont. On a phone the shelf edge and canyons sit at the bottom edge under the legend and the two-line attribution (phone screenshot).
Fix: fit to the fishing grounds on load (roughly 39.0 to 41.4 N, 74.2 to 69.0 W on phones; the whole area on laptops) and remember the last view per browser.

**F4. The tile promises more than the page delivers.** Impact H (credibility), effort S once 2.6 and 2.4 are fixed.
`labsview.TOOLS` says "Step through any week back to 2002" and "with the canyons and offshore spots marked". Uncached weeks fail (P-2 below) and the canyons are unlabeled white dots.
Fix: make the claims true, or soften them until they are.

**F5. Naming.** Impact L, effort S.
The page says "Offshore SST Analyzer", the tile and tab say "Offshore SST", the button says "Fetch SST". Use "Offshore SST" everywhere and plain verbs.

### 2.2 Time controls and animation

**T1. Opens four days stale, and the newest day can't be picked.** Impact H, effort S.
ERDDAP's `jplMURSST41` had Oct 15 (`time[last]` = 2026-10-15T09:00Z) when I checked on Oct 16. The page defaults to today minus 4 (`sidebar.py:42`, Oct 12) and caps the calendar at today minus 2 (`sidebar.py:74`); the calendar shows Oct 15 disabled. The pre-warm (`_prewarm_cache`) and the GFW default also assume today minus 4. Anglers deciding whether to run tomorrow want yesterday's water.
Fix: record the newest available date (the seeder already queries ERDDAP daily; store it, or read `time[last]` with a short cache), default to it, allow it, and tag the newest days "preliminary" (see D4). Seed from today minus 1.

**T2. Changing the date takes a second action.** Impact M, effort S.
You pick a date, then press "Fetch SST". Currents loads on the date change and has a "Now" button.
Fix: load on pick; add "Week back", "Week forward" and "Latest" buttons.

**T3. The week can change under you without saying so.** Impact M, effort S.
Picking Jul 9, 2025 served Jul 2 to 8 with the status "MUR 1km • 7 days loaded (nearest cache: -1d)" while the date box still said Jul 9. `find_nearest_cached` can move the week up to 3 days either way, and `get_sst_multiday` silently moves it back 1 or 2 days when ERDDAP lacks the end date.
Fix: set the date box to the end date actually served and say it in words: "Showing Jul 2 to 8, the closest saved week".

**T4. The animation is a hard cut every 1.5 s with no speed control.** Impact M, effort S to M.
`sidebar.py:156` interval 1500 ms; each tick swaps the overlay URL. Measured: one day per 1.5 s, wrapping 7 to 1. The date isn't on the map while it plays (F2).
Fix: crossfade two overlays, offer 0.5/1/2 s per day, show the date in the status strip. A "change over the week" view (day 7 minus day 1, blue cooled, red warmed) would show at a glance where water moved; it's cheap once the grid is client-side (see E1).

**T5. Day slider hard to read and off-brand.** Impact L, effort S.
Marks are day-of-month only ("6 7 8 ... 12") in low-contrast gray; the slider renders in Dash 4's default purple because the brand rules in `gotone.css` target `rc-slider` classes that Dash 4 doesn't use (its thumb is `.dash-slider-thumb`). The mobile "24 px handle" rule misses for the same reason (thumb measured 16x16 on the phone).
Fix: weekday marks ("Tu 6"), brand blue, a 28 px thumb on touch, all via the Dash 4 class names.

**T6. Lock scale reloads the week from the server and jumps to the last day.** Impact L, effort S.
`lock-scale` is an Input of `fetch_sst_data` (`app.py:1406`): toggling it took 2.2 s and reset the view to day 7.
Fix: keep the frame index; better, recolor in the browser (E1).

### 2.3 Reading the map: legend, tap readout, spots, measure

**R1. Every readout is a server round trip.** Impact H, effort M (part of E1).
Measured from tap to tooltip: 0.8 s on a laptop, 1.7 s on the phone. Readouts read raw arrays from `_raw_data_cache`, which holds only two weeks (`app.py:785`); with a few visitors on different weeks, a tap can trigger a reload of a whole week from disk. There is no hover readout on desktop.
Fix: send the week's grid to the browser (quantized to 0.1 °F, gzip; my estimate is a few hundred KB per week [CHECK by building one]) and read it locally: instant taps, hover on desktop, and readouts that keep working if the worker is busy.

**R2. Spot cards and measurements vanish when the day changes, and an old reading comes back.** Impact H, effort S.
Reproduced on the live site: tap open water (71.0 °F), tap Hudson Canyon (card), step forward: the card disappears and the earlier water reading reappears. Measure Cartwright to Hudson Canyon (88.4 nm), step back: the line and labels disappear, the old water reading reappears, and the sidebar still shows 88.4 nm. Cause: `render_click_marker` (`app.py:1902`) re-runs on every frame change and redraws the click layer from `click-pos` only.
Fix: one "selection" state (reading, spot or measurement) redrawn for the new day, with the temperature updated for that day. That turns a bug into the feature the 7-day view is for: "Hudson Canyon 72 °F on Oct 12, 71 on Oct 11".

**R3. Near a spot or inside The Dump you can't read the temperature where you tapped.** Impact H, effort S.
Tapping 40.70 N, 70.95 W inside The Dump returned "The Dump, 65.0 °F" at the box's center (40.750 N, 70.873 W). Tapping 4 nm east of Coxes Ledge returned the Coxes Ledge card. `find_nearest_poi` uses a fixed 0.08° radius (`pois.py:99`), about 7 px at zoom 7 but about 58 px at zoom 10, and any tap inside the Dump rectangle (`pois.py:110`) becomes the Dump card.
Fix: hit-test the marker in screen pixels (about 14 px for a finger, 8 px for a mouse); inside The Dump, read the tapped point and name the box.

**R4. False precision on top of posterized data.** Impact M, effort M.
`data/convert.py:21` rounds every value to whole °F at ingest; the readouts then print one decimal ("71.0°F"). The cached Oct 6 to 12 week holds only 25 distinct values, so the map is drawn in flat terraces (visible at zoom 10) and any break under 1 °F disappears.
Fix: keep 0.1 °F (float32 or int16 tenths), render smoothly, show "71.3 °F", and optionally draw isotherm lines every 1 or 2 °F.

**R5. The legend says too little and covers water on phones.** Impact M, effort S.
The legend is a fixed 224x91 px box: 15% of the phone map, its right edge 2 px off screen (also on desktop), text 10 to 11 px, ticks 56/60/65/70/74. It has no date, no sign that the scale is this week's, and no sign that the coldest and warmest 5% are clipped to the end colors.
Fix: a compact phone legend (or move it into the status strip), round ticks, "this week's range" label, end markers ("under 56", "over 74"), and the 1 km / 25 km source in words.

**R6. Spots are unlabeled dots.** Impact M, effort S.
32 white circles and a dashed box, no names until tapped. The Spots list only hides or shows markers, in code order with no grouping.
Fix: labels from zoom 9 up; group the list (inshore and wrecks, canyons, banks and ledges); tapping a name flies to the spot and opens its card.

**R7. The measure readout is unreadable and the tool's state is unclear.** Impact M, effort S.
The sidebar readout is `#1e293b` on the `#0a1628` sidebar (`app.py:1821`), a 1.2:1 contrast (visible in the measure screenshot); the "A:" line uses `#334155`. The distance label on the map renders as a plain white Leaflet tooltip because the brand-blue rule is written `.measure-tooltip .leaflet-tooltip` while Leaflet puts the class on the tooltip itself (computed background white). The button reads "Measure", then "📏 Measuring...", then "📏 Measure"; after point B it still says "Measuring..." and the next tap reads a temperature. The bearing is true, while boat compasses are magnetic: variation is 13.1° W at Hudson Canyon, 13.5° W south of Montauk and 14.2° W at Stellwagen (NOAA calculator, WMM-2025).
Fix: light text; fix the selector; a "Clear" button; "187° true (200° magnetic)"; temperatures at both ends; optional multi-leg routes.

### 2.4 Layers: chart, bathymetry, fishing activity

**L1. Chart and bathymetry draw under the SST, where you can't see them.** Impact H, effort M.
Their panes (390, 400) sit below the SST pane (410) at 70% opacity. With Bathymetry on, the shelf edge and canyons are not visible at zoom 9 (screenshot). Anglers read temperature against the 100-fathom line and the canyon walls.
Fix: thin depth contours above the SST (for example 30, 50, 100, 500 and 1000 fathoms, prebuilt from GEBCO or NOAA data as one GeoJSON file) with canyon names; keep the shaded GEBCO layer for people who want it.

**L2. Hidden layers still download their tiles.** Impact M, effort S.
With all three layers off, every load requested 20 GEBCO WMS, 20 NOAA chart WMS and 12 to 24 GFW proxy tiles at 1440x900 (12, 12 and 24 on the phone). GFW proxy calls took up to 15 s, about a fifth returned 404, and each 404 logs a console error. `toggle_layers` (`app.py:2084`) only sets opacity to 0.
Fix: add and remove the layers instead of fading them.

**L3. The fishing-activity date range is shared by every visitor.** Impact M, effort S.
`gfw_tile_proxy` ignores the `?dr=` it is given and uses the process-wide `_gfw_date_range` (`app.py:446`), set by whoever fetched last. A second visitor on another week changes your tiles, and the browser then caches the wrong tiles for 24 hours under your URL.
Fix: read and validate `request.args["dr"]`; key the style and tile caches by it.

**L4. Fishing activity is unexplained.** Impact L, effort S.
No legend. It is Global Fishing Watch's AIS-based "apparent fishing effort", mostly larger commercial vessels; recreational boats rarely show. The newest days may be empty [CHECK GFW's data lag].
Fix: one legend line and a How it works paragraph.

### 2.5 Phone and tablet

**P1. No playback on the map on phones.** Impact H, effort S.
Step and Play live in the drawer, which covers 280 of 390 px, and stepping leaves the drawer open (class stays `drawer-open`), so you can't watch the week change. Currents floats ◀ Play ▶ on the map whenever the sidebar is a drawer.
Fix: reuse `.currents-playbar` for SST, with the date beside it.

**P2. The iPad gets the desktop layout with a 141 px sidebar.** Impact H, effort S.
At 820x1180 the sidebar is 141 px: the date box is 101 px and truncated, "Fetch SST" wraps to two lines, the step buttons are 26x32, the day marks overlap, and the "not helpful" thumb is cut off (screenshot). Currents switches to the drawer below 1200 px and on any touch screen.
Fix: apply the same media query to the SST sidebar.

**P3. The page is wider and taller than the screen.** Impact M, effort S.
Bootstrap's `.row` negative margins put the sidebar at x = -12 and the map 12 px past the right edge at every size (phone map rect -12 to 402: zoom buttons flush to the screen edge, legend and attribution clipped). The header is 80 px but the map and sidebar assume 72 (`mapview.py:7`, `sidebar.py:341`, `gotone.css:54`), so 8 px hang below the screen; clicking the map focuses it and the browser scrolls the page 8 px (measured `scrollTop` 0 to 8), clipping the logo. CLAUDE.md already notes "The SST page still has the 72 px assumption."
Fix: Currents' flex column (`.row { margin: 0 }`, page height `100dvh`, map `flex: 1`).

**P4. Small targets and text.** Impact M, effort S.
Measured on the phone: checkboxes 13x13, spot rows 19 px, slider thumbs 16x16, Measure 31 px, close ✕ 26x32, zoom buttons 30x30; section labels 10.4 px, legend 10 to 11 px.
Fix: 44 px rows with the whole label tappable, larger thumbs, 44 px zoom buttons on touch.

### 2.6 Performance and loading

**P-1. The cached path is fast; keep it.**
Navigation to a usable map took 2.3 to 3.2 s at all three sizes over six runs (Currents: 3.2 to 3.4 s). The week arrives in a 182 KB response; total transfer on the phone was about 0.9 MB (Currents about 3.2 MB). A seeded week nobody has opened yet took about 7 s (PNGs rendered on the fly; one measurement, worth repeating).

**P-2. Uncached weeks fail after more than three minutes.** Impact H, effort M.
Feb 10, 2019 and Aug 20, 2015 both showed "Loading 7-day SST data... This may take up to a minute" for 192 s, then "Error: SST fetch timed out after 90s. Data for this date range may not be available yet." Both weeks exist; from this Mac the same MUR day downloads in 2 s (5 MB). Today's seed run (GitHub Actions, failed) logged `upwell.pfeg` 403, `coastwatch.noaa.gov` 404, `coastwatch.pfeg` MUR 503 and the OISST 404 on every retry. The 90 s budget is only checked between attempts (`erddap.py:334`) while a single day can take 45 s three times. I didn't confirm the cause on Render; check its logs for a returning NOAA block. Render holds 337 cached files (`/api/precache/status`), mostly Jun to Nov 2020 to 2025 weekly plus the seeded 2026 weeks, so most of "back to 2002" depends on this path.
Fix: drop the two dead servers from `config.json`; fix or remove the fallback (D2); a hard total budget of about 25 s with per-request timeouts that fit inside it; a fast, honest failure that offers the nearest saved weeks; optionally queue the week for the seeder and say when it will be ready. Widening the precache (for example every third day, Apr to Dec, 2003 to 2025) needs `MAX_ENTRIES` raised and about 1 GB of disk [CHECK estimate].

**P-3. `/api/precache` is an open GET.** Impact H (ops), effort S.
Anyone, including a crawler, can start an hours-long ERDDAP crawl with their own year range and `delay=0` (`app.py:510`), and the 500-entry eviction would then push seeded weeks out.
Fix: require `CACHE_UPLOAD_TOKEN`, as the upload routes do.

### 2.7 Data honesty: dates, latency, clouds, fallback

**D1. The status always says "MUR 1km".** Impact M, effort S.
`app.py:1574` hard-codes it whatever served the week.
Fix: build it from `dataset_id` and `res_km`, in words ("satellite blend, 1 km").

**D2. The OISST fallback can never work.** Impact H (honesty), effort S to remove, M to fix.
The fallback search picks `ncdcOisst2Agg`, which ended on 2020-04-26 and uses 0 to 360 longitudes, and the query leaves out OISST's `zlev` axis. curl of the app's own query returns 404 "axis#1=zlev ... greater than the axis maximum". The seed logs show the same 404.
Fix: use `ncdcOisst21Agg_LonPM180` (final, ends Oct 1, 2026 today, so about two weeks behind) with a `[(0.0)]` zlev constraint, plus the near-real-time OISST dataset for recent days [CHECK its ERDDAP id]; or remove the fallback and say so. When it is used, label the map plainly: 25 km, broad pattern only.

**D3. Clouds are invisible.** Impact M, effort M.
MUR fills every pixel: all 151,257 water cells are present on every day of the cached weeks I checked, so a break estimated under three days of cloud looks as solid as one seen this morning. The same ERDDAP dataset carries `analysis_error` (estimated error, °C) per pixel.
Fix: fetch it and haze water where it is high, as Currents hazes low-confidence water, with a legend line [CHECK a threshold against a known cloudy week].

**D4. Nothing says the newest days are preliminary.** Impact L, effort S.
ERDDAP's history for `jplMURSST41`: "created at nominal 4-day latency; replaced nrt (1-day latency) version", and "the data for the most recent 7 days is usually revised everyday". `cache.is_stale` treats anything older than 3 days as final, so a week a visitor fetched 4 to 6 days after its end date can keep early values; seeded weeks are rebuilt daily for 10 days, so most are fine.
Fix: tag days within 4 days of today as preliminary in the status strip; refresh user-fetched recent weeks after day 7.

**D5. Foundation temperature isn't explained.** Impact L, effort S (copy).
MUR is built from nighttime measurements, so it doesn't show afternoon surface warming on calm days. Say so in How it works.

### 2.8 Visual polish and consistency with Currents and the GotOne brand

**V1. Share Currents' chrome.** Impact M, effort S.
The light basemap suits SST colors, so keep it, but use the same header (How it works plus ← GotOne Labs), status strip, floating controls, pill segmented controls, dark modal and dark translucent legend (`.currents-legend`).

**V2. Tooltip styles never apply.** Impact L, effort S.
`tooltips.css` and `gotone.css:286` style `.sst-tooltip .leaflet-tooltip`, `.poi-tooltip .leaflet-tooltip` and `.measure-tooltip .leaflet-tooltip`, but Leaflet puts the class on the tooltip element, so the computed style is the plain white default; spot cards have no class at all.
Fix: `.leaflet-tooltip.sst-tooltip` and friends (Currents' `.leaflet-tooltip.currents-tip` is written correctly).

**V3. Developer copy.** Impact M, effort S.
"Fetch SST", "MUR 1km • 7 days loaded", "(nearest cache: -1d)", "1.1 km grid", "Lock scale (30-90°F)", "SST opacity", "v1.0". Suggested: "Load week", "Oct 6 to 12 · satellite blend, 1 km", "closest saved week", "Same colors every week", "Color strength"; move the version out of sight.

**V4. Map clutter and palette.** Impact M, effort M.
The dashed area outline runs diagonally over land from New York Harbor to Maine; clip it to the sea or drop it. In summer and fall the offshore water sits in the top third of the adaptive range, which runs red to dark red, the hardest hues to tell apart at 70% opacity (zoom-10 screenshot). Test a palette with more distinct steps where anglers look, and check it for color-blind readers.

**V5. Emoji in one button.** Impact L, effort S.
The 📏 appears and disappears on the Measure button; CLAUDE.md already warns against icon characters in labels.

### 2.9 Engineering

**E1. Move SST to a client-side engine, the Currents way.** Impact H, effort L.
SST runs 9 server callbacks and 8 clientside ones (plus the shared drawer toggle), all inside a 2,635-line `app.py` beside Currents, the catch map and analytics. Currents keeps Dash to layout and a few forwarders and does all interaction in `assets/currents.js`. For SST that means `layout/sstview.py`, `assets/sst.js` and a data route such as `/api/sst/<end>.bin` (the week's quantized grid plus metadata, ETag cached). Tap and hover readouts, readouts that follow the day, crossfades, recoloring and lock scale, spot labels and a week-at-a-point sparkline all become local, and the single worker only serves data.

**E2. A smoke test before pushes.** Impact M, effort S.
Render deploys every push to `main`. A headless script (load, tap, step, spot, measure, phone drawer) against localhost would have caught R2, R3 and P3. The probe used for this review (scratchpad `probe.mjs`) is a starting point.

## 3. Top improvements

Ranked by value to anglers per unit of work. The three that matter most are marked ★.

1. ★ **Make every week load, and open on the newest day.** Fix the miss path (P-2: dead servers out, hard 25 s budget, honest failure with nearby saved weeks), fix or remove OISST (D2), default to and allow the newest MUR day (T1), say which week was served (T3), lock `/api/precache` (P-3). Today the tool's core promise fails for most dates and its default view is three days older than it needs to be. Effort M.
2. ★ **Explain and date the map.** How it works modal and header link (F1, section 4), a Currents-style status strip with date, source and freshness (F2, D1, D4), a legend that says the scale is this week's (R5), plain copy (V3, F5). This is the visible difference between SST and Currents today. Effort S to M.
3. ★ **Make reading the map right on phones and tablets.** Readout state that survives day changes and shows each day's temperature (R2), tap-where-you-tap near spots and in The Dump (R3), a readable measure tool with magnetic bearing (R7), the floating playbar (P1), the drawer on iPad and touch screens (P2), the flex layout fix (P3), bigger targets (P4) and a first view on the fishing grounds (F3). Effort M.
4. **Depth contours and spot labels above the SST** (L1, R6). Breaks matter most where they meet the shelf edge and canyon heads, and today that structure is invisible. Effort M.
5. **Client-side SST engine** (E1), which brings instant tap and hover readouts (R1), crossfade and speed (T4), recoloring without refetch (T6), and 0.1 °F data (R4). Effort L.
6. **Cloud honesty**: an `analysis_error` haze and legend line (D3). Effort M.
7. **Layer hygiene**: load layers only when on (L2), per-request GFW date range (L3), a fishing-activity legend (L4). Effort S.
8. **Brand and polish**: tooltip selectors (V2), slider styling and marks (T5), outline and palette (V4), emoji (V5), a smoke test (E2). Effort S.

Suggested sprints:
- **Sprint 1, "right and explained" (about a week):** items 1, 2, 3, 7 and the small parts of 8. All of it ships inside the current architecture.
- **Sprint 2, "feels like Currents" (one to two weeks):** items 5, 4 and 6, in that order, since the engine makes contours, labels and the haze simple to add.
- **Later:** a temperature-break overlay (gradient strength from the 0.1 °F grid), the change-over-the-week map (T4), multi-leg routes (R7).

## 4. How it works: draft for SST

Implementation notes: mirror Currents. A `dbc.Modal` (id `sst-about`, title "About GotOne Offshore SST", `scrollable=True`, `size="lg"`, dark content class shared with Currents), opened by a "How it works" button in the header and a link at the bottom of the sidebar, auto-opened once per browser with a `gotone-sst-intro-v1` localStorage flag, and a `how_it_works` event added to `labstats.EVENTS["sst"]` and the analytics contract. Section heads use `.currents-about-h`; the reliability table uses `.currents-about-table` and the `.conf-*` colors. Brackets marked [CHECK] are for Luyen or for after the fixes above.

---

**About GotOne Offshore SST**

Offshore fish follow the water. Tuna, mahi, marlin and the bait they feed on collect where warm and cool water meet, along the edges of warm eddies, and where the continental shelf drops into the canyons. GotOne Offshore SST maps the sea-surface temperature from Cape May, New Jersey to Portland, Maine, out past the canyons, one map for each day, so you can see where those edges are and how they moved over the past week.

WHAT TO LOOK FOR

**Temperature breaks.** A break is a line where the temperature changes quickly, a few degrees in a mile or two. On the map it shows as colors bunched close together. Bait gathers along breaks and the fish that eat it hunt them. A sharp break that holds in the same place for several days is usually a better bet than a faint one that wanders.

**Warm eddies.** Rings of warm Gulf Stream water break off, drift slowly along the edge of the shelf, usually toward the southwest, and can last for weeks [CHECK]. They show as a round or oval patch of warmer color. Their edges carry breaks, and where an eddy pushes warm water up against the shelf or into a canyon the fishing can turn on.

**The shelf edge and the canyons.** The bottom drops from about 100 fathoms at the edge of the shelf to thousands of feet in Hudson, Block, Atlantis, Veatch, Hydrographer and the other canyons. Water running into that edge brings cooler, richer water up and holds bait. A break that lines up with the edge or a canyon head is the classic combination. Turn on Nautical chart or Bathymetry to see the depths [CHECK: until depth lines are drawn above the colors, lower Color strength to see them].

**Cold water near the beach.** In summer, a few days of southwest wind can bring cold bottom water up along the New Jersey shore. It shows as a cold strip close to the beach that can appear in a day or two.

**Change over the week.** Step through the seven days to see whether a break is holding, moving or fading, and which way an eddy is drifting.

HOW TO USE IT

**Pick a day.** The map opens on the newest day available, usually yesterday [CHECK: today it opens four days back]. Pick any other day back to June 2002 in the date box; you'll see that day and the six days before it [CHECK: older weeks that aren't saved yet fail today].

**Step through the week.** Use ◀ and ▶ to move a day at a time, or Play to loop through all seven. The date of the day on the map is shown under it [CHECK: status strip].

**Tap for a temperature.** Tap or click the water to read its temperature on the day shown, with its position.

**Spots.** The white circles are 32 well-known offshore spots, wrecks, canyons and banks, and the dashed box is The Dump. Tap one for its name, position and temperature. Use Spots to show or hide them.

**Measure.** Press Measure and tap two points to get the distance in nautical and statute miles and the bearing from the first point to the second. Tapping near a spot snaps to it. The bearing is true, not magnetic: for a magnetic compass course add about 13 to 14 degrees in this area (2026).

**Layers.** Nautical chart shows NOAA's chart: depths, buoys, wrecks and hazards. Bathymetry shades the depth of the bottom. Fishing activity shows where commercial fishing boats carrying AIS trackers fished during the same seven days, from Global Fishing Watch; most recreational boats don't appear. Color strength fades the temperatures so a layer underneath shows through.

READING THE COLORS

Blue is cold and red is warm. The scale fits each week: it runs from the cool end to the warm end of that week's water across the whole map, leaving out the coldest and warmest 5%, which show in the end colors. That way even a two-degree break stands out. The legend shows the week's range. Because the scale changes from week to week, the same red can mean 68 °F one week and 76 °F the next, so read the legend or tap the map before comparing weeks.

Lock scale fixes the colors at 30 to 90 °F so any two weeks can be compared directly. It also flattens the detail, so leave it off when you're looking for breaks [CHECK if Lock scale changes].

Temperatures are rounded to the nearest whole degree Fahrenheit [CHECK: may change to tenths]. Gray is land or outside the mapped area.

WHERE THE DATA COMES FROM

The map uses MUR (Multi-scale Ultra-high Resolution) sea-surface temperature from NASA's Jet Propulsion Laboratory, served by NOAA CoastWatch. MUR is a daily map on a 1 km grid that blends several satellites with buoys and ships. Infrared sensors (MODIS on NASA's Terra and Aqua satellites, AVHRR on the European MetOp satellites) see fine detail but can't see through clouds. A microwave sensor (AMSR2 on Japan's GCOM-W satellite) sees through clouds but at much coarser detail and not close to shore [CHECK sensor list: from the dataset's metadata, Oct 2026]. MUR is built from nighttime measurements, so it shows the water just below the thin surface layer that warms up on a calm, sunny afternoon.

**Clouds.** Where clouds hide the water from the infrared satellites, MUR fills the gap from the microwave data, from nearby days and from the surrounding water. Every spot on the map has a temperature, but under several days of cloud the detail is a smooth estimate: breaks look softer than they are, or sit where they were a few days ago [CHECK: if a haze is added, "hazed water shows where MUR's own error estimate is high"].

**How fresh it is.** MUR's first version of a day comes out about a day later. A final version replaces it about four days later, so the newest days can still change a little [CHECK: the page should mark them preliminary].

**When MUR isn't available.** The map falls back to NOAA's OISST, a daily map on a 25 km grid. It shows the broad pattern only: no sharp breaks and nothing near the coast. The legend then says 25 km grid [CHECK: the fallback doesn't work today; fix it or delete this paragraph].

HOW RELIABLE IS IT?

| Situation | Confidence | What to expect |
|---|---|---|
| Open water after a clear day or two | High | Breaks and eddy edges are where the map shows them, and temperatures are usually within about a degree of a buoy [CHECK against MUR validation figures]. |
| The newest day or two | Good | A first version that gets revised over the next few days. |
| After several cloudy days | Moderate | The map is filling in. Breaks look softer and can lag the real water by days. Trust the pattern, not the exact line. |
| Within a mile or two of the beach, inlets, bays, Long Island Sound | Moderate | Each 1 km square can mix land and water, and the land mask trims the edge. A nearby buoy or your own reading is better. |
| A calm, sunny afternoon | Check on the water | The map shows the nighttime temperature below the surface film. The top few feet can read a few degrees warmer by afternoon [CHECK magnitude]. |
| After a strong wind or a storm | Changes fast | Wind can move or wipe out a break in a few hours. Look at the newest day before you run. |
| OISST weeks (25 km grid) | Low | Broad pattern only [CHECK: only if the fallback is fixed]. |

SOURCES

- Sea-surface temperature: MUR SST v4.1, NASA JPL, distributed by PO.DAAC (https://podaac.jpl.nasa.gov/dataset/MUR-JPL-L4-GLOB-v4.1), served by NOAA CoastWatch ERDDAP as dataset jplMURSST41 (https://coastwatch.pfeg.noaa.gov/erddap/info/jplMURSST41/index.html). NOAA CoastWatch: https://coastwatch.noaa.gov/
- How MUR is made: Chin, Vazquez-Cuervo and Armstrong (2017), "A multi-scale high-resolution analysis of global sea surface temperature," Remote Sensing of Environment (https://doi.org/10.1016/j.rse.2017.07.029). MUR is part of GHRSST, the Group for High Resolution Sea Surface Temperature (https://www.ghrsst.org/).
- Backup temperatures: NOAA Daily Optimum Interpolation SST (OISST), NOAA NCEI (https://www.ncei.noaa.gov/products/optimum-interpolation-sst) [CHECK: list only if the fallback works].
- Nautical chart: NOAA Office of Coast Survey chart display service (https://nauticalcharts.noaa.gov/data/gis-data-and-services.html).
- Bathymetry: GEBCO (https://www.gebco.net/).
- Fishing activity: Global Fishing Watch, apparent fishing effort (https://globalfishingwatch.org/dataset-and-code-fishing-effort/).
- Spots: Marine Basin's offshore spot list (https://www.marinebasin.com/fishing_spots) and The Dump from Salty Cape (https://saltycape.com/the-dump/) [CHECK: the canyon and bank positions have no recorded source, and whether to credit these sites at all].
- Magnetic variation: NOAA's magnetic field calculator (https://www.ngdc.noaa.gov/geomag/calculators/magcalc.shtml).

For fishing planning and learning, not for navigation. Check the marine forecast before you go.

---

All source links above returned HTTP 200 with `curl -sIL` on Oct 16, 2026 (www.ghrsst.org redirects to ghrsst.org; the DOI resolves to Elsevier).

## Appendix: measurements (Oct 16, 2026, live site)

| What | Laptop 1440x900 | Phone 390x664 | iPad 820x1180 |
|---|---|---|---|
| Navigation to usable map (cached week) | 2.3 to 3.2 s | 2.5 s | 2.6 s |
| Tap to temperature readout | 0.8 s | 1.7 s | not measured |
| Uncached week (2 tries) | 192 s, then error | | |
| Lock scale toggle | 2.2 s, resets to day 7 | | |
| Week data response | 182 KB | 182 KB | 182 KB |
| Total transfer | about 0.9 MB | about 0.9 MB (Currents about 3.2 MB) | |
| Hidden-layer tile requests per load | 20 GEBCO, 20 chart, 12 to 24 GFW | 12, 12, 24 | |
| Sidebar width | 244 px | drawer 280 px | 141 px |
| Smallest targets | | checkbox 13 px, slider thumb 16 px, zoom 30 px | step buttons 26x32 |
| Currents How it works | opens 1.6 s after a first visit | 1.8 s | |
